from app.services.ansible_view import inventory_configuration_yaml
"""Inventory administration and preview routes."""

from app.services.composite_inventory import normalize_source_inventories
from app.services.costly_operation_rate_limit import costly_operation_rate_limit
from app.services.dispatch_progress import dispatch_progress_reporter
from app.services.inventory_dependencies import composite_member_leaf_sources
from app.services.inventory_host_paths import observed_host_variable_paths
from app.services.name_ordering import reserved_name_ordering
from app.services.pagination import paginate_list, page_size_for_user
from app.services.inventory_inspect_bindings import (
    inventory_binding_names,
    packages_for_inventory_bindings,
)
from app.services.project_package_launch import (
    PackageLaunchError,
    package_inventory_binding_fields,
    prepare_inventory_binding_values,
)

from app.routes import (
    CREDENTIAL_TYPE_SATELLITE, CREDENTIAL_TYPE_ZABBIX, CREDENTIAL_TYPE_URL, Credential,
    FILTER_FIELD_CHOICES, FILTER_OPERATOR_CHOICES, Inventory,
    InventoryCacheError, InventoryDependencyError, InventoryResolutionError,
    Project, ProjectStep, _inventory_config_from_form, _inventory_form_data,
    _inventory_form_from_request, _validate_inventory_form, abort, bp,
    current_app, current_user_is_admin, current_username, db, delete_inventory_cache,
    direct_dependants_by_inventory, direct_inventory_dependants, flash,
    inventory_config, inventory_host_count, json, redirect, refresh_inventory,
    render_template, request, resolve_inventory, url_for, jsonify,
)



@bp.get("/inventories/<int:inventory_id>/ansible/configuration")
def inventory_show_ansible_configuration(inventory_id):
    if not current_user_is_admin():
        abort(403)
    inventory = db.get_or_404(Inventory, inventory_id)
    return render_template(
        "show_ansible.html",
        ansible_kind="Configuration",
        ansible_yaml=inventory_configuration_yaml(inventory),
        ansible_note=None,
        resource_kind="Inventory",
        resource_name=inventory.name,
        back_url=url_for("main.inventories") + "#inventory-{}".format(inventory.id),
    )

@bp.get("/inventories")
def inventories():
    if not current_user_is_admin():
        abort(403)

    rows = Inventory.query.order_by(*reserved_name_ordering(Inventory.name)).all()
    pagination = paginate_list(rows, page_size_for_user(current_username()))
    rows = pagination.items

    try:
        dependants_by_inventory = (
            direct_dependants_by_inventory(
                rows
            )
        )

    except InventoryDependencyError as exc:
        current_app.logger.warning(
            "Unable to inspect inventory dependencies: %s",
            exc,
        )

        dependants_by_inventory = {}

    credential_ids = {
        inventory.credential_id
        for inventory in rows
        if inventory.credential_id is not None
    }

    if credential_ids:
        credentials = (
            Credential.query
            .filter(Credential.id.in_(credential_ids))
            .all()
        )
    else:
        credentials = []

    credentials_by_id = {
        credential.id: credential
        for credential in credentials
    }

    project_counts = {}

    for inventory in rows:
        project_ids = {
            project_id
            for project_id, in (
                db.session.query(Project.id)
                .filter(
                    Project.inventory_id
                    == inventory.id
                )
                .all()
            )
        }

        project_ids.update(
            project_id
            for project_id, in (
                db.session.query(
                    ProjectStep.project_id
                )
                .filter(
                    ProjectStep.inventory_id
                    == inventory.id
                )
                .all()
            )
        )

        project_counts[
            inventory.id
        ] = len(project_ids)

    dependency_counts = {
        inventory.id: len(
            dependants_by_inventory.get(
                inventory.id,
                [],
            )
        )
        for inventory in rows
    }

    return render_template(
        "inventories.html",
        inventories=rows,
        credentials_by_id=credentials_by_id,
        project_counts=project_counts,
        dependency_counts=dependency_counts,
        pagination=pagination,
    )

def _render_inventory_form(
    *,
    inventory,
    credentials,
    form_data,
):
    """
    Render the inventory form with all inventory-type choices.
    """

    source_query = (
        Inventory.query
        .order_by(
            *reserved_name_ordering(Inventory.name)
        )
    )

    if inventory is not None:
        source_query = source_query.filter(
            Inventory.id != inventory.id
        )

    source_inventories = (
        source_query.all()
    )

    composite_source_inventories = list(
        source_inventories
    )

    try:
        composite_member_leaf_ids = composite_member_leaf_sources(
            [source.id for source in composite_source_inventories]
        )
    except InventoryDependencyError:
        # Save-time and resolution-time validation remain authoritative.
        # Malformed legacy definitions should not make the edit form unusable.
        composite_member_leaf_ids = {
            source.id: {source.id}
            for source in composite_source_inventories
        }

    return render_template(
        "inventory_form.html",
        inventory=inventory,
        credentials=credentials,
        form_data=form_data,
        source_inventories=source_inventories,
        filter_field_choices=(
            FILTER_FIELD_CHOICES
        ),
        filter_operator_choices=(
            FILTER_OPERATOR_CHOICES
        ),
        composite_source_inventories=(
            composite_source_inventories
        ),
        composite_member_leaf_ids=(
            composite_member_leaf_ids
        ),
    )

def _inventory_credentials():
    """
    Return credentials supported by inventory providers.
    """

    return (
        Credential.query
        .filter(
            Credential.credential_type.in_(
                (
                    CREDENTIAL_TYPE_SATELLITE,
                    CREDENTIAL_TYPE_ZABBIX,
                    CREDENTIAL_TYPE_URL,
                )
            )
        )
        .order_by(
            Credential.name.asc()
        )
        .all()
    )


@bp.get("/inventories/<int:inventory_id>/host-variable-paths")
def inventory_host_variable_paths(inventory_id):
    """Return hostvar paths observed in the cached/resolved inventory."""
    if not current_user_is_admin():
        abort(403)

    inventory = db.get_or_404(Inventory, inventory_id)
    try:
        inventory_data = resolve_inventory(inventory)
    except InventoryResolutionError as exc:
        return jsonify({
            "error": str(exc),
            "host_count": 0,
            "paths": [],
            "truncated": False,
        }), 409

    return jsonify(observed_host_variable_paths(inventory_data))

@bp.route(
    "/inventories/new",
    methods=["GET", "POST"],
)
def inventory_new():
    if not current_user_is_admin():
        abort(403)

    credentials = _inventory_credentials()

    clone_source = None
    clone_value = request.args.get("clone")

    if clone_value:
        try:
            clone_id = int(clone_value)
        except (TypeError, ValueError):
            abort(404)

        clone_source = db.get_or_404(
            Inventory,
            clone_id,
        )

    form_data = _inventory_form_data(
        clone_source
    ) if clone_source is not None else _inventory_form_data()

    if clone_source is not None:
        # A clone is a new Inventory definition. Preserve the source
        # configuration, but force the operator to choose a new name.
        form_data["name"] = ""

    if request.method == "POST":
        form_data = _inventory_form_from_request()

        errors = _validate_inventory_form(form_data)

        if errors:
            for error in errors:
                flash(error, "error")

            return _render_inventory_form(
                inventory=None,
                credentials=credentials,
                form_data=form_data,
            )

        inventory = Inventory(
            name=form_data["name"],
            inventory_type=form_data["inventory_type"],
            endpoint=(
                form_data["endpoint"]
                if form_data["inventory_type"] == "zabbix"
                else ""
            ),
            credential_id=(
                form_data["credential_id"]
                if form_data["inventory_type"] in {
                    "satellite", "zabbix", "netbox", "lightspeed", "ovirt",
                }
                else None
            ),
            verify_tls=(
                form_data["verify_tls"]
                if form_data["inventory_type"] in {
                    "satellite", "zabbix", "netbox", "lightspeed", "ovirt",
                }
                else True
            ),
            enabled=form_data["enabled"],
            config_json=json.dumps(
                _inventory_config_from_form(
                    form_data
                ),
                sort_keys=True,
            ),
            status="never_synced",
        )

        db.session.add(inventory)

        try:
            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to create inventory"
            )

            flash(
                "Unable to create the inventory. "
                "The inventory name may already be in use.",
                "error",
            )

            return _render_inventory_form(
                inventory=None,
                credentials=credentials,
                form_data=form_data,
            )

        flash(
            f'Inventory "{inventory.name}" created.',
            "success",
        )

        return redirect(
            url_for("main.inventories")
        )

    return _render_inventory_form(
        inventory=None,
        credentials=credentials,
        form_data=form_data,
    )

@bp.route(
    "/inventories/<int:inventory_id>/edit",
    methods=["GET", "POST"],
)
def inventory_edit(inventory_id):
    if not current_user_is_admin():
        abort(403)

    inventory = db.get_or_404(
        Inventory,
        inventory_id,
    )

    credentials = _inventory_credentials()

    form_data = _inventory_form_data(inventory)

    if request.method == "POST":
        form_data = _inventory_form_from_request()

        # Inventory type cannot be changed after creation.
        form_data["inventory_type"] = (
            inventory.inventory_type
        )

        errors = _validate_inventory_form(
            form_data,
            inventory=inventory,
        )

        if errors:
            for error in errors:
                flash(error, "error")

            return _render_inventory_form(
                inventory=inventory,
                credentials=credentials,
                form_data=form_data,
            )

        old_config = inventory_config(
            inventory
        )

        old_credential_id = (
            inventory.credential_id
        )

        old_verify_tls = (
            inventory.verify_tls
        )

        old_endpoint = inventory.endpoint

        inventory.name = form_data["name"]
        inventory.endpoint = (
            form_data["endpoint"]
            if inventory.inventory_type
            == "zabbix"
            else ""
        )
        inventory.enabled = form_data["enabled"]

        if inventory.inventory_type in {
            "satellite", "zabbix", "netbox", "lightspeed", "ovirt",
        }:
            inventory.credential_id = (
                form_data["credential_id"]
            )

            inventory.verify_tls = (
                form_data["verify_tls"]
            )

        elif inventory.inventory_type in {
            "static",
            "filtered",
            "composite",
        }:
            inventory.credential_id = None
            inventory.verify_tls = True

        new_config = _inventory_config_from_form(
            form_data
        )

        inventory.config_json = json.dumps(
            new_config,
            sort_keys=True,
        )

        inventory_changed = (
            old_config != new_config
            or old_credential_id
            != inventory.credential_id
            or old_verify_tls
            != inventory.verify_tls
        )

        endpoint_changed = (
            old_endpoint != inventory.endpoint
        )

        if inventory_changed or endpoint_changed:
            inventory.status = "never_synced"
            inventory.last_sync_at = None

        try:
            if inventory_changed or endpoint_changed:
                delete_inventory_cache(
                    inventory
                )

            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to update Inventory %s",
                inventory_id,
            )

            flash(
                "Unable to update the inventory. "
                "The inventory name may already be in use.",
                "error",
            )

            return _render_inventory_form(
                inventory=inventory,
                credentials=credentials,
                form_data=form_data,
            )

        flash(
            f'Inventory "{inventory.name}" updated.',
            "success",
        )

        return redirect(
            url_for("main.inventories")
        )

    return _render_inventory_form(
        inventory=inventory,
        credentials=credentials,
        form_data=form_data,
    )

@bp.post(
    "/inventories/<int:inventory_id>/refresh"
)
@costly_operation_rate_limit("inventory_refresh")
def inventory_refresh(inventory_id):
    if not current_user_is_admin():
        abort(403)

    inventory = db.get_or_404(
        Inventory,
        inventory_id,
    )

    progress = dispatch_progress_reporter(
        request.headers.get("X-Journeyman-Dispatch-Progress", ""),
        current_username(),
        'Inventory — {}'.format(inventory.name),
    )
    progress(
        "prepare",
        "Preparing inventory refresh",
        "Validating the inventory configuration and refresh request.",
    )

    try:
        inventory_type_label = (
            str(inventory.inventory_type or "inventory")
            .replace("_", " ")
            .title()
        )
        progress(
            "refresh",
            "Refreshing {} inventory".format(inventory_type_label),
            (
                "Contacting the configured source and rebuilding the "
                "canonical inventory cache. This can take a while."
            ),
        )
        inventory_data = refresh_inventory(
            inventory
        )

        progress(
            "finalise",
            "Finalising refreshed inventory",
            "Counting resolved hosts and recording refresh status.",
        )
        host_count = inventory_host_count(
            inventory_data
        )

    except InventoryResolutionError as exc:
        progress.fail(str(exc))
        current_app.logger.warning(
            "Unable to refresh Inventory %s: %s",
            inventory.id,
            exc,
        )

        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    success_message = (
        'Inventory "{}" refreshed successfully with {} host{}.'.format(
            inventory.name,
            host_count,
            "" if host_count == 1 else "s",
        )
    )
    progress.done(success_message)
    flash(
        success_message,
        "success",
    )

    diagnostics = _inventory_provider_diagnostics(
        inventory_data
    )
    duplicate_count = diagnostics.get(
        "duplicate_source_records",
        0,
    )
    if isinstance(duplicate_count, int) and duplicate_count > 0:
        source_records = diagnostics.get(
            "source_records",
            host_count,
        )
        duplicate_identities = diagnostics.get(
            "duplicate_identities",
            {},
        )
        identity_count = (
            len(duplicate_identities)
            if isinstance(duplicate_identities, dict)
            else 0
        )
        flash(
            (
                "{} source records resolved to {} unique hosts. "
                "{} duplicate source record{} across {} hostname{} "
                "were preserved for inspection."
            ).format(
                source_records,
                host_count,
                duplicate_count,
                "" if duplicate_count == 1 else "s",
                identity_count,
                "" if identity_count == 1 else "s",
            ),
            "warning",
        )

    return redirect(
        url_for("main.inventories")
    )

def _inventory_preview_host(
    hostname,
    host_variables,
):
    """
    Derive safe display fields from canonical inventory host variables.

    Complete foreman_params and foreman_facts data is deliberately not
    passed to the HTML template.
    """

    if not isinstance(host_variables, dict):
        host_variables = {}

    foreman = host_variables.get(
        "foreman",
        {},
    )

    if not isinstance(foreman, dict):
        foreman = {}

    facts = host_variables.get(
        "foreman_facts",
        {},
    )

    if not isinstance(facts, dict):
        facts = {}

    content_attributes = foreman.get(
        "content_attributes",
        {},
    )

    if not isinstance(content_attributes, dict):
        content_attributes = {}

    distribution_name = (
        facts.get("distribution::name")
        or facts.get("distribution")
        or ""
    )

    distribution_version = (
        facts.get("distribution::version")
        or ""
    )

    operating_system = " ".join(
        str(value).strip()
        for value in (
            distribution_name,
            distribution_version,
        )
        if value
    )

    return {
        "name": hostname,
        "ip": (
            host_variables.get("ansible_host")
            or foreman.get("ipv4")
            or facts.get("network::ipv4_address")
            or ""
        ),
        "operating_system": operating_system,
        "hostgroup": (
            foreman.get("host_group")
            or ""
        ),
        "environment": (
            content_attributes.get(
                "lifecycle_environment_name"
            )
            or ""
        ),
        "organization": (
            foreman.get("organization")
            or ""
        ),
    }


def _safe_inventory_preview(
    inventory_data,
    *,
    limit=100,
):
    """
    Return safe preview rows from canonical inventory JSON.
    """

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars", {})
    )

    if not isinstance(hostvars, dict):
        raise InventoryResolutionError(
            "The resolved inventory has no hostvars dictionary."
        )

    hosts = [
        _inventory_preview_host(
            hostname,
            variables,
        )
        for hostname, variables
        in hostvars.items()
    ]

    hosts.sort(
        key=lambda row: row["name"].lower()
    )

    return {
        "hosts": hosts[:limit],
        "shown": min(
            len(hosts),
            limit,
        ),
        "total": len(hosts),
    }


def _inventory_provider_diagnostics(inventory_data):
    """Return provider diagnostics embedded in canonical inventory metadata."""

    metadata = inventory_data.get("_meta", {})
    if not isinstance(metadata, dict):
        return {}

    diagnostics = metadata.get(
        "journeyman_provider_diagnostics",
        {},
    )
    return diagnostics if isinstance(diagnostics, dict) else {}


def _inventory_hostvars(inventory_data):
    """
    Return the canonical hostvars mapping from resolved inventory data.
    """

    hostvars = (
        inventory_data
        .get("_meta", {})
        .get("hostvars", {})
    )

    if not isinstance(hostvars, dict):
        raise InventoryResolutionError(
            "The resolved inventory has no hostvars dictionary."
        )

    return hostvars


def _composite_inspection_sources(inventory, *, bindings=None):
    """
    Resolve direct composite sources for display-only provenance.

    Provenance is intentionally not added to the canonical inventory
    passed to Ansible.
    """

    if inventory.inventory_type != "composite":
        return []

    config = inventory_config(inventory)
    source_inventory_ids = config.get(
        "source_inventory_ids",
        [],
    )

    if not isinstance(source_inventory_ids, list):
        return []

    sources = []

    for source_inventory_id in source_inventory_ids:
        try:
            source_inventory_id = int(source_inventory_id)
        except (TypeError, ValueError):
            continue

        source_inventory = db.session.get(
            Inventory,
            source_inventory_id,
        )

        if source_inventory is None:
            continue

        source_data = resolve_inventory(source_inventory, bindings=bindings)

        sources.append({
            "inventory": source_inventory,
            "data": source_data,
        })

    normalized = normalize_source_inventories(
        [
            (source["inventory"].name, source["data"])
            for source in sources
        ],
        config.get("normalize_hostnames", "none"),
        config.get("append_domain", ""),
    )

    for source, (_source_name, source_data) in zip(sources, normalized):
        source["data"] = source_data
        source["hostvars"] = _inventory_hostvars(source_data)

    return sources


@bp.route(
    "/inventories/<int:inventory_id>/preview",
    methods=["GET", "POST"],
)
def inventory_preview(inventory_id):
    if not current_user_is_admin():
        abort(403)

    inventory = db.get_or_404(
        Inventory,
        inventory_id,
    )

    required_bindings = sorted(
        inventory_binding_names(inventory),
        key=str.lower,
    )
    binding_package = None
    binding_fields = []
    bindings = {}
    binding_form_values = {}

    if required_bindings:
        packages = packages_for_inventory_bindings(
            inventory,
            required_bindings,
        )

        if not packages:
            flash(
                'Inventory "{}" requires inventory binding{} {}, but no enabled Package '
                'using this inventory provides all required bindings.'.format(
                    inventory.name,
                    "s" if len(required_bindings) != 1 else "",
                    ", ".join('"{}"'.format(name) for name in required_bindings),
                ),
                "error",
            )
            return redirect(url_for("main.inventories"))

        requested_package_id = (
            request.form.get("package_id")
            if request.method == "POST"
            else request.args.get("package_id")
        )
        try:
            requested_package_id = int(requested_package_id) if requested_package_id else None
        except (TypeError, ValueError):
            requested_package_id = None

        binding_package = next(
            (package for package in packages if package.id == requested_package_id),
            packages[0],
        )

        try:
            if request.method == "POST":
                errors, binding_fields, bindings = prepare_inventory_binding_values(
                    package=binding_package,
                    binding_names=required_bindings,
                    form=request.form,
                    runtime_values={},
                )
                binding_form_values = {
                    key: value
                    for key, value in request.form.items()
                    if key.startswith("package_value_")
                }
                if errors:
                    return render_template(
                        "inventory_binding_prompt.html",
                        inventory=inventory,
                        packages=packages,
                        selected_package=binding_package,
                        required_bindings=required_bindings,
                        fields=binding_fields,
                        errors=errors,
                    ), 400
            else:
                binding_fields = package_inventory_binding_fields(
                    binding_package,
                    required_bindings,
                    runtime_values={},
                )
                return render_template(
                    "inventory_binding_prompt.html",
                    inventory=inventory,
                    packages=packages,
                    selected_package=binding_package,
                    required_bindings=required_bindings,
                    fields=binding_fields,
                    errors=[],
                )
        except PackageLaunchError as exc:
            flash(str(exc), "error")
            return redirect(url_for("main.inventories"))

    try:
        inventory_data = resolve_inventory(
            inventory,
            bindings=bindings,
        )

        preview = _safe_inventory_preview(
            inventory_data,
            limit=100,
        )

        provider_diagnostics = _inventory_provider_diagnostics(
            inventory_data
        )

        hostvars = _inventory_hostvars(
            inventory_data
        )

        hostnames = sorted(
            hostvars,
            key=str.lower,
        )

        selected_hostname = (
            request.form.get("host", "")
            if request.method == "POST"
            else request.args.get("host", "")
        ).strip()

        if selected_hostname not in hostvars:
            selected_hostname = (
                hostnames[0]
                if hostnames
                else ""
            )

        selected_hostvars = (
            hostvars.get(selected_hostname, {})
            if selected_hostname
            else {}
        )

        source_inspection = (
            _composite_inspection_sources(
                inventory,
                bindings=bindings,
            )
        )

        selected_sources = []

        for source in source_inspection:
            source_variables = source[
                "hostvars"
            ].get(selected_hostname)

            selected_sources.append({
                "inventory": source["inventory"],
                "present": source_variables is not None,
                "variables": (
                    source_variables
                    if isinstance(source_variables, dict)
                    else {}
                ),
            })

        config = inventory_config(
            inventory
        )

        raw_inventory = json.dumps(
            inventory_data,
            indent=2,
            sort_keys=True,
        )

        selected_host_json = json.dumps(
            selected_hostvars,
            indent=2,
            sort_keys=True,
        )

        for source in selected_sources:
            source["variables_json"] = json.dumps(
                source["variables"],
                indent=2,
                sort_keys=True,
            )

    except InventoryResolutionError as exc:
        current_app.logger.warning(
            "Unable to inspect Inventory %s: %s",
            inventory.id,
            exc,
        )

        flash(
            str(exc),
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    except Exception:
        current_app.logger.exception(
            "Unexpected error inspecting Inventory %s",
            inventory.id,
        )

        flash(
            "An unexpected error occurred while resolving "
            "the inventory.",
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    return render_template(
        "inventory_preview.html",
        inventory=inventory,
        credential=inventory.credential,
        preview=preview,
        preview_hosts=preview["hosts"],
        organization=config.get(
            "organization",
            "",
        ),
        hostnames=hostnames,
        selected_hostname=selected_hostname,
        selected_host_json=selected_host_json,
        selected_sources=selected_sources,
        provider_diagnostics=provider_diagnostics,
        raw_inventory=raw_inventory,
        inspect_binding_package=binding_package,
        inspect_binding_form_values=binding_form_values,
    )

@bp.post(
    "/inventories/<int:inventory_id>/delete"
)
def inventory_delete(inventory_id):
    if not current_user_is_admin():
        abort(403)

    inventory = db.get_or_404(
        Inventory,
        inventory_id,
    )

    try:
        dependant_inventories = (
            direct_inventory_dependants(
                inventory.id
            )
        )

    except InventoryDependencyError as exc:
        current_app.logger.warning(
            "Unable to inspect dependencies before deleting "
            "Inventory %s: %s",
            inventory.id,
            exc,
        )

        flash(
            "Unable to verify whether this inventory is used "
            "by another inventory. It was not deleted.",
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    project_step = (
        ProjectStep.query
        .filter(
            ProjectStep.inventory_id
            == inventory.id
        )
        .first()
    )

    if project_step is not None:
        flash(
            (
                f'Inventory "{inventory.name}" cannot be '
                "deleted because it is used as a project "
                "step override."
            ),
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    if dependant_inventories:
        dependant_names = ", ".join(
            dependant.name
            for dependant in dependant_inventories
        )

        flash(
            'Inventory "{}" cannot be deleted because it is '
            "used by: {}.".format(
                inventory.name,
                dependant_names,
            ),
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    project = (
        Project.query
        .filter(Project.inventory_id == inventory.id)
        .first()
    )

    if project is not None:
        flash(
            f'Inventory "{inventory.name}" cannot be deleted '
            "because it is assigned to one or more projects.",
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    inventory_name = inventory.name

    try:
        delete_inventory_cache(
            inventory
        )

    except InventoryCacheError as exc:
        current_app.logger.warning(
            "Unable to delete cache for Inventory %s: %s",
            inventory.id,
            exc,
        )

        flash(
            "The inventory cache could not be removed. "
            "The inventory was not deleted.",
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    try:
        db.session.delete(inventory)
        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Unable to delete Inventory %s",
            inventory_id,
        )

        flash(
            f'Unable to delete inventory "{inventory_name}".',
            "error",
        )

        return redirect(
            url_for("main.inventories")
        )

    flash(
        f'Inventory "{inventory_name}" deleted.',
        "success",
    )

    return redirect(
        url_for("main.inventories")
    )
