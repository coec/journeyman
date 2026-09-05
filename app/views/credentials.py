from app.services.ansible_view import credential_configuration_yaml
"""Credential administration and owner-only reveal routes."""

import yaml

from app.routes import (
    CREDENTIAL_TYPE_CHOICES, CREDENTIAL_TYPE_LABELS, CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES,
    CREDENTIAL_TYPE_MACHINE, CREDENTIAL_TYPE_WINDOWS, CREDENTIAL_TYPE_SATELLITE,
    CREDENTIAL_TYPE_SOURCE_CONTROL, CREDENTIAL_TYPE_VAULT,
    CREDENTIAL_TYPE_ZABBIX, CREDENTIAL_TYPE_URL, CREDENTIAL_TYPE_CUSTOM, Credential, ProjectStep, SECURITY_SCOPE_CHOICES,
    VALID_CREDENTIAL_TYPES, VALID_SECURITY_SCOPES, _clean, abort, bp,
    can_administer, current_app, current_user_is_admin, current_username, db, flash, jsonify, or_,
    record_audit_event, redirect, render_template, request, url_for,
    validate_credential_environment_variables,
)

from app.credential_extra_vars import CredentialExtraVarsError, validate_credential_extra_vars
from app.custom_credentials import (
    CustomCredentialError,
    validate_custom_credential_definition,
    validate_custom_credential_data,
)
from app.services.pagination import paginate_list, page_size_for_user
from app.services.outbound_security import OutboundSecurityError, validate_outbound_url
from app.services.url_credentials import URLCredentialError, normalise_url_credential_data
from app.models import Environment



def _parse_credential_extra_vars(text):
    text = str(text or "").strip()
    if not text:
        return {}
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CredentialExtraVarsError(
            "Credential extra_vars are not valid YAML: {}".format(exc)
        ) from exc
    return validate_credential_extra_vars(value)




def _parse_custom_credential_definition(text):
    text = str(text or "").strip()
    if not text:
        raise CustomCredentialError("Custom credential definition is required.")
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CustomCredentialError(
            "Custom credential definition is not valid YAML: {}".format(exc)
        ) from exc
    return validate_custom_credential_definition(value)


def _dump_custom_credential_definition(value):
    if not value:
        return ""
    return yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()


def _custom_fields_for_form(definition, values=None, submitted=None):
    values = values or {}
    submitted = submitted or {}
    rows = []
    for field in (definition or {}).get("fields", []):
        field_id = field["id"]
        if field.get("secret"):
            value = ""
        elif field_id in submitted:
            value = submitted[field_id]
        else:
            value = values.get(field_id, "")
        rows.append({**field, "value": value})
    return rows

def _dump_credential_extra_vars(value):
    value = value or {}
    if not value:
        return ""
    return yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()


_CREDENTIAL_REVEAL_FIELDS = {
    CREDENTIAL_TYPE_MACHINE: (
        ("password", "Password"),
        ("ssh_private_key", "SSH private key"),
        ("ssh_key_passphrase", "SSH key passphrase"),
        ("become_password", "Become password"),
    ),
    CREDENTIAL_TYPE_WINDOWS: (
        ("password", "Password"),
    ),
    CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES: (
        ("password", "Secret"),
    ),
    CREDENTIAL_TYPE_VAULT: (
        ("vault_password", "Vault password"),
    ),
    CREDENTIAL_TYPE_SOURCE_CONTROL: (
        ("password", "Password or access token"),
        ("ssh_private_key", "SSH private key"),
        ("ssh_key_passphrase", "SSH key passphrase"),
    ),
    CREDENTIAL_TYPE_SATELLITE: (
        ("password", "Password"),
    ),
    CREDENTIAL_TYPE_ZABBIX: (
        ("token", "API token"),
    ),
    CREDENTIAL_TYPE_URL: (
        ("password", "Password / client secret"),
        ("token", "API token"),
    ),
}


def _revealed_credential_values(credential, credential_data):
    values = []

    if credential.credential_type == CREDENTIAL_TYPE_CUSTOM:
        custom = validate_custom_credential_data(credential_data)
        for field in custom["definition"]["fields"]:
            if not field.get("secret"):
                continue
            value = custom["values"].get(field["id"], "")
            if value not in (None, ""):
                values.append(
                    {
                        "key": field["id"],
                        "label": field["label"],
                        "value": str(value),
                    }
                )
        return values

    for key, label in _CREDENTIAL_REVEAL_FIELDS.get(
        credential.credential_type,
        (),
    ):
        value = credential_data.get(key)

        if value not in (None, ""):
            values.append(
                {
                    "key": key,
                    "label": label,
                    "value": str(value),
                }
            )

    return values




@bp.get("/credentials/<int:credential_id>/ansible/configuration")
def credential_show_ansible_configuration(credential_id):
    if not current_user_is_admin():
        abort(403)
    credential = db.get_or_404(Credential, credential_id)
    return render_template(
        "show_ansible.html",
        ansible_kind="Configuration",
        ansible_yaml=credential_configuration_yaml(credential),
        ansible_note=(
            "Stored secret values cannot be read back by Journeyman. "
            "Populated secret fields are represented with Ansible variable placeholders."
        ),
        resource_kind="Credential",
        resource_name=credential.name,
        back_url=url_for("main.credentials"),
    )

@bp.route("/credentials")
def credentials():
    """
    Display stored credentials.
    """

    q = _clean(request.args.get("q"))
    username = current_username()

    query = Credential.query

    if q:
        query = query.filter(
            or_(
                Credential.name.ilike("%{}%".format(q)),
                Credential.description.ilike("%{}%".format(q)),
                Credential.owner.ilike("%{}%".format(q)),
                Credential.username.ilike("%{}%".format(q)),
            )
        )

    credentials = query.order_by(Credential.name).all()
    pagination = paginate_list(credentials, page_size_for_user(username))
    credentials = pagination.items

    selected = None

    selected_id = request.args.get(
        "selected",
        type=int,
    )

    if selected_id is not None:
        selected = db.session.get(Credential, selected_id)

    all_credentials = Credential.query.all()

    stats = {
        "total": len(all_credentials),
        "machine": sum(
            credential.credential_type == "machine"
            for credential in all_credentials
        ),
        "windows": sum(
            credential.credential_type == "windows"
            for credential in all_credentials
        ),
        "vault": sum(
            credential.credential_type == "vault"
            for credential in all_credentials
        ),
        "source_control": sum(
            credential.credential_type == "source_control"
            for credential in all_credentials
        ),
        "satellite": sum(
            credential.credential_type == "satellite"
            for credential in all_credentials
        ),
        "zabbix": sum(
            credential.credential_type == "zabbix"
            for credential in all_credentials
        ),
        "url": sum(
            credential.credential_type == "url"
            for credential in all_credentials
        ),
        "custom": sum(
            credential.credential_type == "custom"
            for credential in all_credentials
        ),
    }

    administerable_credential_ids = {
        credential.id
        for credential in credentials
        if can_administer(credential)
    }
    revealable_credential_ids = {
        credential.id
        for credential in credentials
        if credential.owner == username
        and credential.encrypted_data is not None
    }

    from app.services.secret_lifecycle import credential_too_old
    old_credential_ids = {
        credential.id for credential in credentials if credential_too_old(credential)
    }

    return render_template(
        "credentials.html",
        credentials=credentials,
        credential_type_labels=CREDENTIAL_TYPE_LABELS,
        administerable_credential_ids=administerable_credential_ids,
        revealable_credential_ids=revealable_credential_ids,
        old_credential_ids=old_credential_ids,
        old_credential_count=len(old_credential_ids),
        pagination=pagination,
        pagination_args={"q": q},
    )

@bp.post("/credentials/<int:credential_id>/reveal")
def credential_reveal(credential_id):
    """Reveal stored secret values to the credential owner only."""

    credential = db.get_or_404(
        Credential,
        credential_id,
    )

    if credential.owner != current_username():
        record_audit_event(
            "credential.secret_reveal",
            result="denied",
            object_type="credential",
            object_id=credential.id,
            object_name=credential.name,
        )
        abort(403)

    try:
        credential_data = credential.get_credential_data()
        values = _revealed_credential_values(
            credential,
            credential_data,
        )
    except Exception:
        current_app.logger.exception(
            "Unable to reveal Credential %s",
            credential.id,
        )
        record_audit_event(
            "credential.secret_reveal",
            result="failure",
            object_type="credential",
            object_id=credential.id,
            object_name=credential.name,
        )
        response = jsonify(
            {
                "error": "Unable to decrypt the stored credential.",
            }
        )
        response.status_code = 500
    else:
        record_audit_event(
            "credential.secret_reveal",
            result="success",
            object_type="credential",
            object_id=credential.id,
            object_name=credential.name,
            details={
                "revealed_fields": [
                    item["key"] for item in values
                ],
            },
        )
        response = jsonify(
            {
                "credential_id": credential.id,
                "values": values,
            }
        )

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, private, max-age=0"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@bp.route("/credentials/new", methods=["GET", "POST"])
def credential_new():
    """
    Create and encrypt a Journeyman credential.
    """

    username = current_username()

    form_data = {
        "name": "",
        "description": "",
        "credential_type": CREDENTIAL_TYPE_MACHINE,
        "security_scope": "private",
        "username": "",
        "username_environment_variable": "",
        "secret_environment_variable": "",
        "become_method": "sudo",
        "become_user": "root",
        "vault_id": "",
        "satellite_host": "",
        "url": "",
        "url_auth_mode": "bearer",
        "url_token_url": "",
        "url_scope": "",
        "url_token_prefix": "",
        "machine_extra_vars": "",
        "windows_extra_vars": "",
        "custom_definition": "",
        "custom_fields": [],
    }

    if request.method == "POST":
        form_data = {
            "name": _clean(request.form.get("name")),
            "description": _clean(
                request.form.get("description")
            ),
            "credential_type": _clean(
                request.form.get("credential_type")
            ),
            "security_scope": _clean(
                request.form.get("security_scope")
            ),
            "username": _clean(
                request.form.get("username")
                or request.form.get("windows_username")
                or request.form.get("environment_username")
                or request.form.get("source_control_username")
                or request.form.get("satellite_username")
                or request.form.get("url_username")
            ),
            "username_environment_variable": _clean(
                request.form.get("username_environment_variable")
            ),
            "secret_environment_variable": _clean(
                request.form.get("secret_environment_variable")
            ),
            "become_method": _clean(
                request.form.get("become_method")
            ),
            "become_user": _clean(
                request.form.get("become_user")
            ),
            "vault_id": _clean(
                request.form.get("vault_id")
            ),
            "satellite_host": _clean(
                request.form.get("satellite_host")
            ).rstrip("/"),
            "url": _clean(request.form.get("url")).rstrip("/"),
            "url_auth_mode": _clean(request.form.get("url_auth_mode")),
            "url_token_url": _clean(request.form.get("url_token_url")),
            "url_scope": _clean(request.form.get("url_scope")),
            "url_token_prefix": _clean(request.form.get("url_token_prefix")),
            "machine_extra_vars": str(
                request.form.get("machine_extra_vars") or ""
            ),
            "windows_extra_vars": str(
                request.form.get("windows_extra_vars") or ""
            ),
            "custom_definition": str(
                request.form.get("custom_definition") or ""
            ),
            "custom_fields": [],
        }

        credential_type = form_data["credential_type"]
        errors = []

        if not form_data["name"]:
            errors.append("Name is required.")

        if credential_type not in VALID_CREDENTIAL_TYPES:
            errors.append("A valid credential type is required.")

        if (
            form_data["security_scope"]
            not in VALID_SECURITY_SCOPES
        ):
            errors.append("A valid security scope is required.")

        credential_data = {}

        if credential_type == CREDENTIAL_TYPE_MACHINE:
            password = request.form.get(
                "machine_password",
                "",
            )
            ssh_private_key = request.form.get(
                "machine_ssh_private_key",
                "",
            )
            ssh_key_passphrase = request.form.get(
                "machine_ssh_key_passphrase",
                "",
            )
            become_password = request.form.get(
                "become_password",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a machine credential."
                )

            if not password and not ssh_private_key:
                errors.append(
                    "A machine credential requires either a "
                    "password or an SSH private key."
                )

            if password:
                credential_data["password"] = password

            if ssh_private_key:
                credential_data["ssh_private_key"] = (
                    ssh_private_key
                )

            if ssh_key_passphrase:
                credential_data["ssh_key_passphrase"] = (
                    ssh_key_passphrase
                )

            if form_data["become_method"]:
                credential_data["become_method"] = (
                    form_data["become_method"]
                )

            if form_data["become_user"]:
                credential_data["become_user"] = (
                    form_data["become_user"]
                )

            if become_password:
                credential_data["become_password"] = (
                    become_password
                )

            try:
                extra_vars = _parse_credential_extra_vars(
                    form_data["machine_extra_vars"]
                )
                if extra_vars:
                    credential_data["extra_vars"] = extra_vars
            except CredentialExtraVarsError as exc:
                errors.append(str(exc))

        elif credential_type == CREDENTIAL_TYPE_WINDOWS:
            password = request.form.get(
                "windows_password",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a Windows credential."
                )

            if not password:
                errors.append(
                    "Password is required for a Windows credential."
                )

            if password:
                credential_data["password"] = password

            try:
                extra_vars = _parse_credential_extra_vars(
                    form_data["windows_extra_vars"]
                )
                if extra_vars:
                    credential_data["extra_vars"] = extra_vars
            except CredentialExtraVarsError as exc:
                errors.append(str(exc))

        elif (
            credential_type
            == CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES
        ):
            password = request.form.get(
                "environment_secret",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for an environment-variable credential."
                )

            if not password:
                errors.append(
                    "Secret is required for an environment-variable credential."
                )

            variable_errors = validate_credential_environment_variables(
                form_data["username_environment_variable"],
                form_data["secret_environment_variable"],
            )
            errors.extend(variable_errors)

            if password:
                credential_data["password"] = password

            if form_data["username_environment_variable"]:
                credential_data["username_environment_variable"] = (
                    form_data["username_environment_variable"]
                )

            if form_data["secret_environment_variable"]:
                credential_data["secret_environment_variable"] = (
                    form_data["secret_environment_variable"]
                )

        elif credential_type == CREDENTIAL_TYPE_VAULT:
            vault_password = request.form.get(
                "vault_password",
                "",
            )

            if not vault_password:
                errors.append(
                    "Vault password is required."
                )

            if form_data["vault_id"]:
                credential_data["vault_id"] = (
                    form_data["vault_id"]
                )

            if vault_password:
                credential_data["vault_password"] = (
                    vault_password
                )

        elif (
            credential_type
            == CREDENTIAL_TYPE_SOURCE_CONTROL
        ):
            password = request.form.get(
                "source_control_password",
                "",
            )
            ssh_private_key = request.form.get(
                "source_control_ssh_private_key",
                "",
            )
            ssh_key_passphrase = request.form.get(
                "source_control_ssh_key_passphrase",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a source-control "
                    "credential."
                )

            if not password and not ssh_private_key:
                errors.append(
                    "A source-control credential requires either "
                    "a password or an SSH private key."
                )

            if password:
                credential_data["password"] = password

            if ssh_private_key:
                credential_data["ssh_private_key"] = (
                    ssh_private_key
                )

            if ssh_key_passphrase:
                credential_data["ssh_key_passphrase"] = (
                    ssh_key_passphrase
                )

        elif credential_type == CREDENTIAL_TYPE_SATELLITE:
            satellite_password = request.form.get(
                "satellite_password",
                "",
            )

            satellite_host = form_data["satellite_host"]

            if not satellite_host:
                errors.append(
                    "Satellite URL is required."
                )

            else:
                try:
                    satellite_host = validate_outbound_url(
                        satellite_host, purpose="Satellite"
                    )
                    form_data["satellite_host"] = satellite_host
                except OutboundSecurityError as exc:
                    errors.append(str(exc))

            if not form_data["username"]:
                errors.append(
                    "Username is required for a "
                    "Satellite credential."
                )

            if not satellite_password:
                errors.append(
                    "Password is required for a "
                    "Satellite credential."
                )

            if satellite_host:
                credential_data["host"] = satellite_host

            if satellite_password:
                credential_data["password"] = (
                    satellite_password
                )

        elif credential_type == CREDENTIAL_TYPE_URL:
            password = request.form.get("url_password", "")
            token = request.form.get("url_token", "")
            credential_data = {
                "url": form_data["url"],
                "auth_mode": form_data["url_auth_mode"],
                "token_url": form_data["url_token_url"],
                "scope": form_data["url_scope"],
                "token_prefix": form_data["url_token_prefix"],
            }
            if password:
                credential_data["password"] = password
            if token:
                credential_data["token"] = token
            try:
                credential_data = normalise_url_credential_data(
                    credential_data, username=form_data["username"]
                )
            except URLCredentialError as exc:
                errors.append(str(exc))

        elif credential_type == CREDENTIAL_TYPE_CUSTOM:
            try:
                definition = _parse_custom_credential_definition(
                    form_data["custom_definition"]
                )
                credential_data = {
                    "definition": definition,
                    "values": {
                        field["id"]: ""
                        for field in definition["fields"]
                    },
                }
                form_data["custom_fields"] = _custom_fields_for_form(definition)
            except CustomCredentialError as exc:
                errors.append(str(exc))

        elif credential_type == CREDENTIAL_TYPE_ZABBIX:
            zabbix_token = (
                request.form.get(
                    "zabbix_token",
                    "",
                )
                or ""
            ).strip()

            if zabbix_token:
                credential_data["token"] = (
                    zabbix_token
                )

            elif not credential_data.get("token"):
                errors.append(
                    "Zabbix API token is required."
                )

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "credential_form.html",
                credential=None,
                form_data=form_data,
                owner=username,
                credential_type_choices=(
                    CREDENTIAL_TYPE_CHOICES
                ),
                security_scope_choices=(
                    SECURITY_SCOPE_CHOICES
                ),
            )

        credential = Credential(
            name=form_data["name"],
            description=form_data["description"],
            owner=username,
            security_scope=form_data["security_scope"],
            credential_type=credential_type,
            username=form_data["username"],
        )

        try:
            credential.set_credential_data(
                credential_data
            )

            db.session.add(credential)
            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to create Credential"
            )

            flash(
                "Unable to create the credential. A credential "
                "with this name may already exist for this owner.",
                "error",
            )

            return render_template(
                "credential_form.html",
                credential=None,
                form_data=form_data,
                owner=username,
                credential_type_choices=(
                    CREDENTIAL_TYPE_CHOICES
                ),
                security_scope_choices=(
                    SECURITY_SCOPE_CHOICES
                ),
            )

        if credential_type == CREDENTIAL_TYPE_CUSTOM:
            flash(
                "Custom credential definition created. Enter all field values before use.",
                "success",
            )
            return redirect(
                url_for("main.credential_edit", credential_id=credential.id)
            )

        flash("Credential created.", "success")

        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    return render_template(
        "credential_form.html",
        credential=None,
        form_data=form_data,
        owner=username,
        credential_type_choices=CREDENTIAL_TYPE_CHOICES,
        security_scope_choices=SECURITY_SCOPE_CHOICES,
    )

@bp.route(
    "/credentials/<int:credential_id>/edit",
    methods=["GET", "POST"],
)
def credential_edit(credential_id):
    """
    Edit a credential administered by its owner or an administrator.

    Secret fields left blank retain their current values.
    Supplied secret fields replace the corresponding stored values.
    """

    credential = db.get_or_404(
        Credential,
        credential_id,
    )

    if not can_administer(credential):
        abort(403)

    username = current_username()

    try:
        existing_data = credential.get_credential_data()
    except Exception:
        current_app.logger.exception(
            "Unable to decrypt Credential %s",
            credential_id,
        )

        flash(
            "Unable to read the stored credential data.",
            "error",
        )

        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    form_data = {
        "name": credential.name,
        "description": credential.description or "",
        "credential_type": credential.credential_type,
        "security_scope": credential.security_scope,
        "username": credential.username or "",
        "username_environment_variable": existing_data.get(
            "username_environment_variable",
            "",
        ),
        "secret_environment_variable": existing_data.get(
            "secret_environment_variable",
            "",
        ),
        "become_method": existing_data.get(
            "become_method",
            "",
        ),
        "become_user": existing_data.get(
            "become_user",
            "",
        ),
        "vault_id": existing_data.get(
            "vault_id",
            "",
        ),
        "satellite_host": existing_data.get(
            "host",
            "",
        ),
        "url": existing_data.get("url", ""),
        "url_auth_mode": existing_data.get("auth_mode", "bearer"),
        "url_token_url": existing_data.get("token_url", ""),
        "url_scope": existing_data.get("scope", ""),
        "url_token_prefix": existing_data.get("token_prefix", ""),
        "machine_extra_vars": (
            _dump_credential_extra_vars(existing_data.get("extra_vars"))
            if credential.credential_type == CREDENTIAL_TYPE_MACHINE
            else ""
        ),
        "windows_extra_vars": (
            _dump_credential_extra_vars(existing_data.get("extra_vars"))
            if credential.credential_type == CREDENTIAL_TYPE_WINDOWS
            else ""
        ),
        "custom_definition": (
            _dump_custom_credential_definition(existing_data.get("definition"))
            if credential.credential_type == CREDENTIAL_TYPE_CUSTOM
            else ""
        ),
        "custom_fields": (
            _custom_fields_for_form(
                existing_data.get("definition") or {},
                existing_data.get("values") or {},
            )
            if credential.credential_type == CREDENTIAL_TYPE_CUSTOM
            else []
        ),
    }


    if request.method == "POST":
        form_data = {
            "name": _clean(
                request.form.get("name")
            ),
            "description": _clean(
                request.form.get("description")
            ),
            "credential_type": _clean(
                request.form.get("credential_type")
            ),
            "security_scope": _clean(
                request.form.get("security_scope")
            ),
            "username": _clean(
                request.form.get("username")
                or request.form.get("windows_username")
                or request.form.get("environment_username")
                or request.form.get(
                    "source_control_username"
                )
                or request.form.get(
                    "satellite_username"
                )
                or request.form.get("url_username")
            ),
            "username_environment_variable": _clean(
                request.form.get("username_environment_variable")
            ),
            "secret_environment_variable": _clean(
                request.form.get("secret_environment_variable")
            ),
            "become_method": _clean(
                request.form.get("become_method")
            ),
            "become_user": _clean(
                request.form.get("become_user")
            ),
            "vault_id": _clean(
                request.form.get("vault_id")
            ),
            "satellite_host": _clean(
                request.form.get("satellite_host")
            ).rstrip("/"),
            "url": _clean(request.form.get("url")).rstrip("/"),
            "url_auth_mode": _clean(request.form.get("url_auth_mode")),
            "url_token_url": _clean(request.form.get("url_token_url")),
            "url_scope": _clean(request.form.get("url_scope")),
            "url_token_prefix": _clean(request.form.get("url_token_prefix")),
            "machine_extra_vars": str(
                request.form.get("machine_extra_vars") or ""
            ),
            "windows_extra_vars": str(
                request.form.get("windows_extra_vars") or ""
            ),
            "custom_definition": (
                _dump_custom_credential_definition(existing_data.get("definition"))
                if credential.credential_type == CREDENTIAL_TYPE_CUSTOM
                else str(request.form.get("custom_definition") or "")
            ),
            "custom_fields": [],
        }

        credential_type = form_data["credential_type"]
        errors = []

        if not form_data["name"]:
            errors.append("Name is required.")

        if credential_type not in VALID_CREDENTIAL_TYPES:
            errors.append(
                "A valid credential type is required."
            )

        if (
            form_data["security_scope"]
            not in VALID_SECURITY_SCOPES
        ):
            errors.append(
                "A valid security scope is required."
            )

        if (
            credential_type != credential.credential_type
            and CREDENTIAL_TYPE_CUSTOM in {
                credential_type, credential.credential_type
            }
        ):
            errors.append(
                "Custom credentials cannot be converted to or from another "
                "credential type; create a new credential instead."
            )

        if credential_type != credential.credential_type:
            credential_data = {}
        else:
            credential_data = dict(existing_data)

        if credential_type == CREDENTIAL_TYPE_MACHINE:
            password = request.form.get(
                "machine_password",
                "",
            )

            ssh_private_key = request.form.get(
                "machine_ssh_private_key",
                "",
            )

            ssh_key_passphrase = request.form.get(
                "machine_ssh_key_passphrase",
                "",
            )

            become_password = request.form.get(
                "become_password",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a machine "
                    "credential."
                )

            if password:
                credential_data["password"] = password

            if ssh_private_key:
                credential_data["ssh_private_key"] = (
                    ssh_private_key
                )

            if ssh_key_passphrase:
                credential_data["ssh_key_passphrase"] = (
                    ssh_key_passphrase
                )

            if form_data["become_method"]:
                credential_data["become_method"] = (
                    form_data["become_method"]
                )
            else:
                credential_data.pop(
                    "become_method",
                    None,
                )

            if form_data["become_user"]:
                credential_data["become_user"] = (
                    form_data["become_user"]
                )
            else:
                credential_data.pop(
                    "become_user",
                    None,
                )

            if become_password:
                credential_data["become_password"] = (
                    become_password
                )

            if (
                not credential_data.get("password")
                and not credential_data.get(
                    "ssh_private_key"
                )
            ):
                errors.append(
                    "A machine credential requires either a "
                    "password or an SSH private key."
                )

            try:
                extra_vars = _parse_credential_extra_vars(
                    form_data["machine_extra_vars"]
                )
                if extra_vars:
                    credential_data["extra_vars"] = extra_vars
                else:
                    credential_data.pop("extra_vars", None)
            except CredentialExtraVarsError as exc:
                errors.append(str(exc))

        elif credential_type == CREDENTIAL_TYPE_WINDOWS:
            password = request.form.get(
                "windows_password",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a Windows credential."
                )

            if password:
                credential_data["password"] = password

            if not credential_data.get("password"):
                errors.append(
                    "Password is required for a Windows credential."
                )

            try:
                extra_vars = _parse_credential_extra_vars(
                    form_data["windows_extra_vars"]
                )
                if extra_vars:
                    credential_data["extra_vars"] = extra_vars
                else:
                    credential_data.pop("extra_vars", None)
            except CredentialExtraVarsError as exc:
                errors.append(str(exc))

        elif (
            credential_type
            == CREDENTIAL_TYPE_ENVIRONMENT_VARIABLES
        ):
            password = request.form.get(
                "environment_secret",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for an environment-variable credential."
                )

            if password:
                credential_data["password"] = password

            if not credential_data.get("password"):
                errors.append(
                    "Secret is required for an environment-variable credential."
                )

            variable_errors = validate_credential_environment_variables(
                form_data["username_environment_variable"],
                form_data["secret_environment_variable"],
            )
            errors.extend(variable_errors)

            credential_data["username_environment_variable"] = (
                form_data["username_environment_variable"]
            )
            credential_data["secret_environment_variable"] = (
                form_data["secret_environment_variable"]
            )

        elif credential_type == CREDENTIAL_TYPE_VAULT:
            vault_password = request.form.get(
                "vault_password",
                "",
            )

            if vault_password:
                credential_data["vault_password"] = (
                    vault_password
                )

            if form_data["vault_id"]:
                credential_data["vault_id"] = (
                    form_data["vault_id"]
                )
            else:
                credential_data.pop(
                    "vault_id",
                    None,
                )

            if not credential_data.get("vault_password"):
                errors.append(
                    "Vault password is required."
                )

        elif (
            credential_type
            == CREDENTIAL_TYPE_SOURCE_CONTROL
        ):
            password = request.form.get(
                "source_control_password",
                "",
            )

            ssh_private_key = request.form.get(
                "source_control_ssh_private_key",
                "",
            )

            ssh_key_passphrase = request.form.get(
                "source_control_ssh_key_passphrase",
                "",
            )

            if not form_data["username"]:
                errors.append(
                    "Username is required for a source-control "
                    "credential."
                )

            if password:
                credential_data["password"] = password

            if ssh_private_key:
                credential_data["ssh_private_key"] = (
                    ssh_private_key
                )

            if ssh_key_passphrase:
                credential_data["ssh_key_passphrase"] = (
                    ssh_key_passphrase
                )

            if (
                not credential_data.get("password")
                and not credential_data.get(
                    "ssh_private_key"
                )
            ):
                errors.append(
                    "A source-control credential requires "
                    "either a password or an SSH private key."
                )
        elif credential_type == CREDENTIAL_TYPE_SATELLITE:
            satellite_host = form_data["satellite_host"]

            satellite_password = request.form.get(
                "satellite_password",
                "",
            )

            if not satellite_host:
                errors.append(
                    "Satellite URL is required."
                )

            else:
                try:
                    satellite_host = validate_outbound_url(
                        satellite_host, purpose="Satellite"
                    )
                    form_data["satellite_host"] = satellite_host
                except OutboundSecurityError as exc:
                    errors.append(str(exc))

            if not form_data["username"]:
                errors.append(
                    "Username is required for a "
                    "Satellite credential."
                )

            credential_data["host"] = satellite_host

            # Blank during edit means retain the current password.
            if satellite_password:
                credential_data["password"] = (
                    satellite_password
                )

            elif not credential_data.get("password"):
                errors.append(
                    "Password is required for a "
                    "Satellite credential."
                )

        elif credential_type == CREDENTIAL_TYPE_URL:
            password = request.form.get("url_password", "")
            token = request.form.get("url_token", "")
            for key, value in (
                ("url", form_data["url"]),
                ("auth_mode", form_data["url_auth_mode"]),
                ("token_url", form_data["url_token_url"]),
                ("scope", form_data["url_scope"]),
                ("token_prefix", form_data["url_token_prefix"]),
            ):
                credential_data[key] = value
            if password:
                credential_data["password"] = password
            if token:
                credential_data["token"] = token
            try:
                credential_data = normalise_url_credential_data(
                    credential_data, username=form_data["username"]
                )
            except URLCredentialError as exc:
                errors.append(str(exc))

        elif credential_type == CREDENTIAL_TYPE_CUSTOM:
            try:
                if credential.credential_type == CREDENTIAL_TYPE_CUSTOM:
                    definition = validate_custom_credential_definition(
                        existing_data.get("definition")
                    )
                    previous_values = existing_data.get("values") or {}
                else:
                    definition = _parse_custom_credential_definition(
                        form_data["custom_definition"]
                    )
                    previous_values = {}

                values = {}
                submitted_non_secret = {}
                for field in definition["fields"]:
                    field_id = field["id"]
                    posted = request.form.get("custom_value_{}".format(field_id), "")
                    if field.get("secret") and not posted:
                        value = previous_values.get(field_id, "")
                    else:
                        value = str(posted or "")
                    values[field_id] = value
                    if not field.get("secret"):
                        submitted_non_secret[field_id] = value
                    if not value.strip():
                        errors.append("{} is required.".format(field["label"]))

                credential_data = {
                    "definition": definition,
                    "values": values,
                }
                validate_custom_credential_data(credential_data)
                form_data["custom_fields"] = _custom_fields_for_form(
                    definition,
                    values,
                    submitted=submitted_non_secret,
                )
            except CustomCredentialError as exc:
                errors.append(str(exc))

        if errors:
            for error in errors:
                flash(error, "error")

            return render_template(
                "credential_form.html",
                credential=credential,
                form_data=form_data,
                owner=credential.owner,
                credential_type_choices=(
                    CREDENTIAL_TYPE_CHOICES
                ),
                security_scope_choices=(
                    SECURITY_SCOPE_CHOICES
                ),
            )

        credential.name = form_data["name"]
        credential.description = form_data["description"]
        credential.security_scope = (
            form_data["security_scope"]
        )
        credential.credential_type = credential_type
        credential.username = form_data["username"]

        try:
            credential.set_credential_data(
                credential_data
            )

            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unable to update Credential %s",
                credential_id,
            )

            flash(
                "Unable to update the credential. Another "
                "credential with this name may already exist.",
                "error",
            )

            return render_template(
                "credential_form.html",
                credential=credential,
                form_data=form_data,
                owner=credential.owner,
                credential_type_choices=(
                    CREDENTIAL_TYPE_CHOICES
                ),
                security_scope_choices=(
                    SECURITY_SCOPE_CHOICES
                ),
            )

        flash(
            f'Credential "{credential.name}" updated.',
            "success",
        )

        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    return render_template(
        "credential_form.html",
        credential=credential,
        form_data=form_data,
        owner=credential.owner,
        credential_type_choices=CREDENTIAL_TYPE_CHOICES,
        security_scope_choices=SECURITY_SCOPE_CHOICES,
    )

@bp.post(
    "/credentials/<int:credential_id>/delete"
)
def credential_delete(credential_id):
    """
    Delete a credential when it is not used by any project step.
    """

    credential = db.get_or_404(
        Credential,
        credential_id,
    )

    if not can_administer(credential):
        abort(403)

    project_step = (
        db.session.query(ProjectStep)
        .filter(
            ProjectStep.credentials.any(
                Credential.id == credential.id
            )
        )
        .first()
    )

    if project_step is not None:
        flash(
            f'Credential "{credential.name}" cannot be deleted '
            "because it is used by one or more project steps.",
            "error",
        )

        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    environment = Environment.query.filter_by(
        proxy_credential_id=credential.id
    ).first()
    if environment is not None:
        flash(
            f'Credential "{credential.name}" cannot be deleted because it is '
            f'used as the build proxy for Environment "{environment.name}".',
            "error",
        )
        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    credential_name = credential.name

    try:
        db.session.delete(credential)
        db.session.commit()

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Unable to delete Credential %s",
            credential_id,
        )

        flash(
            f'Unable to delete credential "{credential_name}".',
            "error",
        )

        return redirect(
            url_for(
                "main.credentials",
                selected=credential.id,
            )
        )

    flash(
        f'Credential "{credential_name}" deleted.',
        "success",
    )

    return redirect(
        url_for("main.credentials")
    )
