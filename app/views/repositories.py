from app.services.ansible_view import repository_configuration_yaml
"""Source repository administration routes."""

from app.routes import (
    Credential, GitError, ProjectStep, Repository, _clean, abort, bp, current_app,
    current_user_is_admin, current_username, datetime, db, flash, or_, redirect,
    remove_repository_checkout, render_template, request, sync_repository,
    timezone, url_for,
)
from app.credential_types import CREDENTIAL_TYPE_SOURCE_CONTROL
from app.services.pagination import paginate_list, page_size_for_user
from app.services.builtin_automation import (
    BUILTIN_REPOSITORY_URL,
    ensure_builtin_admin_automation,
)
from app.services.costly_operation_rate_limit import costly_operation_rate_limit
from app.services.name_ordering import reserved_name_ordering
from app.services.outbound_security import (
    OutboundSecurityError,
    validate_repository_url,
)
from app.services.git import (
    GitError as RepositoryValidationError,
    validate_directory_repository_path,
)

def _source_control_credentials():
    return (
        Credential.query
        .filter_by(credential_type=CREDENTIAL_TYPE_SOURCE_CONTROL)
        .order_by(Credential.name.asc(), Credential.id.asc())
        .all()
    )


def _render_repository_form(repository):
    return render_template(
        "repository_form.html",
        repository=repository,
        source_control_credentials=_source_control_credentials(),
    )


def _apply_repository_credential_from_form(repository):
    raw_credential_id = _clean(request.form.get("credential_id"))
    repository.credential_id = None

    if raw_credential_id:
        try:
            credential_id = int(raw_credential_id)
        except ValueError as exc:
            raise RepositoryValidationError(
                "Source Control credential is invalid."
            ) from exc

        credential = db.session.get(Credential, credential_id)
        if (
            credential is None
            or credential.credential_type != CREDENTIAL_TYPE_SOURCE_CONTROL
        ):
            raise RepositoryValidationError(
                "Source Control credential is missing or invalid."
            )
        repository.credential_id = credential.id

    if (
        repository.repository_type == "git"
        and str(repository.url or "").lower().startswith("https://")
        and repository.credential_id is None
    ):
        raise RepositoryValidationError(
            "HTTPS repositories require a Source Control credential."
        )




@bp.get("/repositories/<int:repository_id>/ansible/configuration")
def repository_show_ansible_configuration(repository_id):
    if not current_user_is_admin():
        abort(403)
    repository = db.get_or_404(Repository, repository_id)
    if repository.url == BUILTIN_REPOSITORY_URL:
        abort(404)
    return render_template(
        "show_ansible.html",
        ansible_kind="Configuration",
        ansible_yaml=repository_configuration_yaml(repository),
        ansible_note=None,
        resource_kind="Repository",
        resource_name=repository.name,
        back_url=url_for("main.repositories"),
    )

@bp.get("/repositories")
def repositories():
    q = _clean(request.args.get("q"))
    status = _clean(request.args.get("status"))

    query = Repository.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Repository.name.ilike(like),
                Repository.description.ilike(like),
                Repository.url.ilike(like),
                Repository.directory_path.ilike(like),
                Repository.repository_type.ilike(like),
            )
        )

    if status:
        query = query.filter(Repository.status == status)

    rows = query.order_by(*reserved_name_ordering(Repository.name)).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    rows = pagination.items

    selected = None
    selected_id = request.args.get("selected", type=int)

    if selected_id:
        selected = db.session.get(Repository, selected_id)
    elif rows:
        selected = rows[0]

    stats = {
        "total": Repository.query.count(),
        "updated": Repository.query.filter(
            Repository.status == "up_to_date"
        ).count(),
        "failed": Repository.query.filter(
            Repository.status == "failed"
        ).count(),
        "never": Repository.query.filter(
            Repository.status == "never_synced"
        ).count(),
    }

    return render_template(
        "repositories.html",
        repositories=rows,
        selected=selected,
        stats=stats,
        q=q,
        status=status,
        pagination=pagination,
        pagination_args={"q": q, "status": status},
    )


@bp.route("/repositories/new", methods=["GET", "POST"])
def repository_new():
    if not current_user_is_admin():
        abort(403)

    if request.method == "POST":
        repository_type = _clean(request.form.get("repository_type")) or "git"
        repository = Repository(
            name=_clean(request.form.get("name")),
            description=_clean(request.form.get("description")),
            repository_type=repository_type,
            url=_clean(request.form.get("url")),
            directory_path=_clean(request.form.get("directory_path")),
            default_branch=_clean(
                request.form.get("default_branch")
            ) or "main",
        )

        if not repository.name:
            flash("Name is required.", "error")
            return _render_repository_form(repository)

        if repository.repository_type not in {"git", "directory"}:
            flash("Repository type must be Git or Directory.", "error")
            return _render_repository_form(repository)

        if repository.repository_type == "git":
            repository.directory_path = ""
            if not repository.url:
                flash("Repository URL is required for Git repositories.", "error")
                return _render_repository_form(repository)
            if repository.url != BUILTIN_REPOSITORY_URL:
                try:
                    repository.url = validate_repository_url(repository.url)
                except OutboundSecurityError as exc:
                    flash(str(exc), "error")
                    return _render_repository_form(repository)
            try:
                _apply_repository_credential_from_form(repository)
            except RepositoryValidationError as exc:
                flash(str(exc), "error")
                return _render_repository_form(repository)
        else:
            repository.credential_id = None
            repository.url = ""
            repository.default_branch = "main"
            try:
                repository.directory_path = validate_directory_repository_path(
                    repository.directory_path,
                    repositories=Repository.query.filter_by(
                        repository_type="directory"
                    ).all(),
                )
            except RepositoryValidationError as exc:
                flash(str(exc), "error")
                return _render_repository_form(repository)

        db.session.add(repository)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Repository name must be unique.", "error")
            return _render_repository_form(repository)

        flash(
            "Repository created. Sync it to prepare its execution snapshot.",
            "success",
        )
        return redirect(
            url_for("main.repositories", selected=repository.id)
        )

    return _render_repository_form(None)


@bp.route(
    "/repositories/<int:repository_id>/edit",
    methods=["GET", "POST"],
)
def repository_edit(repository_id):
    if not current_user_is_admin():
        abort(403)

    repository = db.get_or_404(Repository, repository_id)

    if request.method == "POST":
        previous_source = (
            repository.repository_type or "git",
            repository.url or "",
            repository.directory_path or "",
            repository.default_branch or "main",
            repository.credential_id,
        )
        repository.name = _clean(request.form.get("name"))
        repository.description = _clean(request.form.get("description"))
        repository.repository_type = _clean(request.form.get("repository_type")) or "git"
        repository.url = _clean(request.form.get("url"))
        repository.directory_path = _clean(request.form.get("directory_path"))
        repository.default_branch = (
            _clean(request.form.get("default_branch")) or "main"
        )

        if not repository.name:
            flash("Name is required.", "error")
            return _render_repository_form(repository)

        if repository.repository_type not in {"git", "directory"}:
            flash("Repository type must be Git or Directory.", "error")
            return _render_repository_form(repository)

        if repository.repository_type == "git":
            repository.directory_path = ""
            if not repository.url:
                flash("Repository URL is required for Git repositories.", "error")
                return _render_repository_form(repository)
            if repository.url != BUILTIN_REPOSITORY_URL:
                try:
                    repository.url = validate_repository_url(repository.url)
                except OutboundSecurityError as exc:
                    flash(str(exc), "error")
                    return _render_repository_form(repository)
            try:
                _apply_repository_credential_from_form(repository)
            except RepositoryValidationError as exc:
                flash(str(exc), "error")
                return _render_repository_form(repository)
        else:
            repository.credential_id = None
            repository.url = ""
            repository.default_branch = "main"
            try:
                repository.directory_path = validate_directory_repository_path(
                    repository.directory_path,
                    repositories=Repository.query.filter_by(
                        repository_type="directory"
                    ).all(),
                    exclude_repository_id=repository.id,
                )
            except RepositoryValidationError as exc:
                flash(str(exc), "error")
                return _render_repository_form(repository)

        current_source = (
            repository.repository_type or "git",
            repository.url or "",
            repository.directory_path or "",
            repository.default_branch or "main",
            repository.credential_id,
        )
        if current_source != previous_source:
            repository.status = "never_synced"
            repository.last_sync_at = None
            repository.last_sync_message = None
            repository.last_commit = None
            repository.last_commit_message = None
            repository.last_commit_author = None
            repository.last_commit_at = None

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Repository name must be unique.", "error")
            return _render_repository_form(repository)

        flash("Repository updated.", "success")
        return redirect(
            url_for("main.repositories", selected=repository.id)
        )

    return _render_repository_form(repository)


@bp.post("/repositories/<int:repository_id>/sync")
@costly_operation_rate_limit("repository_sync")
def repository_sync(repository_id):
    if not current_user_is_admin():
        abort(403)

    repository = db.get_or_404(Repository, repository_id)

    repository.status = "syncing"
    db.session.commit()

    try:
        if repository.url == BUILTIN_REPOSITORY_URL:
            ensure_builtin_admin_automation()
            flash(
                f"{repository.name} refreshed from the installed Journeyman files.",
                "success",
            )
        else:
            commit = sync_repository(
                repository,
                current_app.config["REPOSITORY_ROOT"],
                token=None,
            )

            repository.status = "up_to_date"
            repository.last_sync_at = datetime.now(timezone.utc)
            repository.last_sync_message = (
                "Directory snapshot refreshed successfully."
                if repository.repository_type == "directory"
                else "Repository synchronized successfully."
            )
            repository.last_commit = commit.sha
            repository.last_commit_message = commit.message
            repository.last_commit_author = commit.author
            repository.last_commit_at = commit.committed_at

            if repository.repository_type == "directory":
                flash(f"{repository.name} directory snapshot refreshed.", "success")
            else:
                flash(f"{repository.name} synchronized.", "success")

    except GitError as exc:
        repository.status = "failed"
        repository.last_sync_at = datetime.now(timezone.utc)
        repository.last_sync_message = str(exc)

        flash(f"Sync failed: {exc}", "error")

    db.session.commit()

    return redirect(
        url_for("main.repositories", selected=repository.id)
    )


@bp.post("/repositories/<int:repository_id>/delete")
def repository_delete(repository_id):
    """
    Delete an unused repository and its local checkout.

    Repositories referenced by project steps cannot be deleted.
    Historical job repository snapshots do not prevent deletion.
    """

    if not current_user_is_admin():
        abort(403)

    repository = db.get_or_404(
        Repository,
        repository_id,
    )

    project_step = (
        ProjectStep.query
        .filter(
            ProjectStep.repository_id == repository.id
        )
        .first()
    )

    if project_step is not None:
        flash(
            f'Repository "{repository.name}" cannot be deleted '
            "because it is used by one or more project steps.",
            "error",
        )

        return redirect(
            url_for(
                "main.repositories",
                selected=repository.id,
            )
        )

    repository_name = repository.name

    try:
        remove_repository_checkout(
            repository,
            current_app.config["REPOSITORY_ROOT"],
        )

        db.session.delete(repository)
        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Unable to delete Repository %s",
            repository_id,
        )

        flash(
            f'Unable to delete repository "{repository_name}".',
            "error",
        )

        return redirect(
            url_for(
                "main.repositories",
                selected=repository.id,
            )
        )

    flash(
        f'Repository "{repository_name}" deleted.',
        "success",
    )

    return redirect(
        url_for("main.repositories")
    )
