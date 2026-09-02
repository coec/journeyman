"""Journeyman web authentication and authorisation context."""

import hmac
import stat
import threading
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.services.audit import record_audit_event


DEVELOPMENT_USERNAME = "acal002"
DEVELOPMENT_ADMINS = {"acal002"}

bp = Blueprint("auth", __name__)


class AuthenticationError(RuntimeError):
    """Raised when an identity cannot be authenticated."""



_LOGIN_FAILURES = defaultdict(deque)
_LOGIN_FAILURE_LOCK = threading.Lock()


def _login_rate_limit_settings():
    return (
        int(current_app.config.get("LOGIN_RATE_LIMIT_ATTEMPTS", 10)),
        int(current_app.config.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300)),
    )


def _login_rate_limit_keys(username):
    source = str(request.remote_addr or "unknown")
    username_key = str(username or "").strip().casefold()
    return (
        ("account_source", username_key, source),
        ("source", source),
    )


def _prune_login_failures(events, now, window_seconds):
    cutoff = now - window_seconds
    while events and events[0] <= cutoff:
        events.popleft()


def _login_rate_limited(username, now=None):
    attempts, window_seconds = _login_rate_limit_settings()
    if attempts <= 0 or window_seconds <= 0:
        return False
    now = time.monotonic() if now is None else now
    with _LOGIN_FAILURE_LOCK:
        for key in _login_rate_limit_keys(username):
            events = _LOGIN_FAILURES[key]
            _prune_login_failures(events, now, window_seconds)
            if len(events) >= attempts:
                return True
    return False


def _record_login_failure(username, now=None):
    attempts, window_seconds = _login_rate_limit_settings()
    if attempts <= 0 or window_seconds <= 0:
        return
    now = time.monotonic() if now is None else now
    with _LOGIN_FAILURE_LOCK:
        for key in _login_rate_limit_keys(username):
            events = _LOGIN_FAILURES[key]
            _prune_login_failures(events, now, window_seconds)
            events.append(now)


def _clear_login_failures(username):
    with _LOGIN_FAILURE_LOCK:
        for key in _login_rate_limit_keys(username):
            if key[0] == "account_source":
                _LOGIN_FAILURES.pop(key, None)


def _rate_limited_login_response(username, next_url):
    current_app.logger.warning(
        "Login rate limit exceeded for %s from %s",
        username,
        request.remote_addr,
    )
    record_audit_event(
        "authentication.rate_limited",
        result="failure",
        actor_username=username,
        details={"reason": "too_many_attempts"},
    )
    flash("Too many login attempts. Please try again later.", "error")
    response = render_template("login.html", next_url=next_url)
    return response, 429, {"Retry-After": str(_login_rate_limit_settings()[1])}

def _normalise_guid(value):
    value = str(value or "").strip().lower()
    return value or None


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _revoke_auth_session(session_id, *, now=None):
    if not session_id:
        return
    from app import db
    from app.models import AuthSession

    row = db.session.get(AuthSession, str(session_id))
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = now or _utc_now()
    db.session.commit()


def _directory_revalidation_due(row, now):
    interval = int(
        current_app.config.get(
            "AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS",
            60,
        )
    )
    if interval < 0:
        raise RuntimeError(
            "AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS cannot be negative."
        )
    checked_at = _as_utc(row.directory_checked_at)
    return checked_at is None or interval == 0 or (
        now - checked_at
    ).total_seconds() >= interval


def _revalidate_directory_identity(identity, row, now):
    from app.services.directory import (
        DirectoryAuthenticationError,
        DirectoryError,
    )
    from app.services.directory_settings import (
        get_or_create_directory_settings,
    )
    from app.services.directory import get_directory_client

    username = str(identity.get("username") or "").strip()

    try:
        settings = get_or_create_directory_settings()
        resolved = get_directory_client(settings).resolve_user_access(username)
    except DirectoryAuthenticationError:
        row.revoked_at = now
        from app import db
        db.session.commit()
        record_audit_event(
            "authentication.session_revoked",
            result="failure",
            actor_username=username,
            authenticated_via="ldap",
            details={"reason": "directory_identity_no_longer_valid"},
        )
        return False
    except DirectoryError as exc:
        current_app.logger.warning(
            "Unable to revalidate Active Directory session for %s: %s",
            username,
            exc,
        )
        from app.security_logging import record_security_rejection
        record_security_rejection(
            "directory_revalidation_failure",
            status_code=503,
            reason="directory_backend_unavailable",
        )
        return None

    resolved_guid = _normalise_guid(resolved.user.object_guid)
    expected_guid = _normalise_guid(identity.get("user_object_guid"))
    if not resolved_guid or resolved_guid != expected_guid:
        row.revoked_at = now
        from app import db
        db.session.commit()
        record_audit_event(
            "authentication.session_revoked",
            result="failure",
            actor_username=username,
            authenticated_via="ldap",
            details={"reason": "directory_identity_changed"},
        )
        return False

    identity["username"] = resolved.user.username
    identity["display_name"] = resolved.user.display_name
    identity["role"] = resolved.role
    identity["group_names"] = [
        group.sam_account_name for group in resolved.groups
    ]
    identity["group_object_guids"] = [
        group.object_guid for group in resolved.groups
    ]
    session["journeyman_identity"] = identity

    row.username = resolved.user.username
    row.user_object_guid = resolved_guid
    row.directory_checked_at = now
    from app import db
    db.session.commit()
    return True


def _validate_auth_session(identity):
    from app import db
    from app.models import AuthSession

    session_id = str(session.get("journeyman_session_id") or "").strip()
    if not session_id:
        return None

    row = db.session.get(AuthSession, session_id)
    if row is None or row.revoked_at is not None:
        return None

    username = str(identity.get("username") or "").strip()
    object_guid = _normalise_guid(identity.get("user_object_guid"))
    if row.username.casefold() != username.casefold():
        return None
    if _normalise_guid(row.user_object_guid) != object_guid:
        return None

    now = _utc_now()

    if str(identity.get("authenticated_via") or "ldap") == "fallback":

        from app.services.fallback_admin import (
            active_fallback_activation,
            fallback_admin_activation_is_non_expiring,
        )

        activation = active_fallback_activation(now=now)
        if activation is None:
            row.revoked_at = now
            db.session.commit()
            return None

        activated_at = _as_utc(activation.activated_at)
        activation_expires_at = _as_utc(activation.expires_at)
        non_expiring = fallback_admin_activation_is_non_expiring(activation)
        if _as_utc(row.created_at) < activated_at:
            row.revoked_at = now
            db.session.commit()
            return None
        if not non_expiring and _as_utc(row.expires_at) > activation_expires_at:
            row.expires_at = activation_expires_at
            db.session.commit()

        g.break_glass_activated_at = activated_at
        g.break_glass_expires_at = None if non_expiring else activation_expires_at
        g.break_glass_non_expiring = non_expiring

    if _as_utc(row.expires_at) <= now:
        row.revoked_at = now
        db.session.commit()
        return None

    if (
        str(identity.get("authenticated_via") or "ldap") == "ldap"
        and _directory_revalidation_due(row, now)
    ):
        revalidated = _revalidate_directory_identity(identity, row, now)
        if revalidated is False:
            return None
        if revalidated is None:
            # Fail closed for this request but retain the server-side session so
            # redundant-directory recovery does not force a new login.
            return False

    last_seen = _as_utc(row.last_seen_at)
    if last_seen is None or (now - last_seen).total_seconds() >= 60:
        row.last_seen_at = now
        db.session.commit()

    return row


def _load_session_identity():
    identity = session.get("journeyman_identity")

    if not isinstance(identity, dict):
        return False

    auth_session = _validate_auth_session(identity)
    if auth_session is None:
        session.clear()
        return False
    if auth_session is False:
        return False

    username = str(identity.get("username") or "").strip()
    role = str(identity.get("role") or "").strip()

    if not username or role not in {"Administrator", "User"}:
        session.pop("journeyman_identity", None)
        return False

    g.authenticated_username = username
    g.authenticated_role = role
    g.authenticated_display_name = str(
        identity.get("display_name") or username
    ).strip()
    g.authenticated_user_object_guid = _normalise_guid(
        identity.get("user_object_guid")
    )
    g.authenticated_group_names = frozenset(
        str(value).strip()
        for value in identity.get("group_names", ())
        if str(value).strip()
    )
    g.authenticated_group_object_guids = frozenset(
        value
        for value in (
            _normalise_guid(item)
            for item in identity.get("group_object_guids", ())
        )
        if value
    )
    g.authenticated_via = str(
        identity.get("authenticated_via") or "ldap"
    )
    g.authenticated_session_id = auth_session.session_id
    return True


def load_authenticated_user():
    """Load the authenticated identity into ``flask.g``."""

    g.authenticated_username = None
    g.authenticated_role = None
    g.authenticated_display_name = None
    g.authenticated_group_names = frozenset()
    g.authenticated_user_object_guid = None
    g.authenticated_group_object_guids = frozenset()
    g.authenticated_via = None
    g.authenticated_session_id = None
    g.break_glass_activated_at = None
    g.break_glass_expires_at = None
    g.break_glass_non_expiring = False

    if current_app.config.get("AUTHENTICATION_DISABLED", False):
        g.authenticated_username = DEVELOPMENT_USERNAME
        g.authenticated_role = (
            "Administrator"
            if DEVELOPMENT_USERNAME in DEVELOPMENT_ADMINS
            else "User"
        )
        g.authenticated_display_name = DEVELOPMENT_USERNAME
        return

    _load_session_identity()


def authentication_required():
    """Redirect anonymous browser requests to the login page."""

    if current_app.config.get("AUTHENTICATION_DISABLED", False):
        return None

    # Runner API calls use their own runner UUID/secret (and, for dispatched
    # jobs, dispatch-token) authentication.  They must not be intercepted by
    # the interactive browser/session login gate, otherwise a valid remote
    # runner receives a 302 redirect to /login instead of the API response.
    runner_api_endpoints = {
        "main.runner_register_api",
        "main.runner_unregister_api",
        "main.runner_heartbeat_api",
        "main.runner_environment_sync_claim_api",
        "main.runner_environment_sync_complete_api",
        "main.runner_job_claim_api",
        "main.runner_job_execution_data_api",
        "main.runner_job_repository_artifact_api",
        "main.runner_job_start_api",
        "main.runner_job_control_api",
        "main.runner_job_refresh_inventories_api",
        "main.runner_job_complete_api",
        "main.runner_slice_execution_data_api",
        "main.runner_slice_repository_artifact_api",
        "main.runner_slice_start_api",
        "main.runner_slice_control_api",
        "main.runner_slice_output_api",
        "main.runner_slice_complete_api",
        "main.runner_signal_api",
        "main.zabbix_signal_api",
    }

    if request.path.startswith("/api/v1/"):
        return None

    if request.endpoint in {
        "auth.login",
        "auth.logout",
        "static",
    } or request.endpoint in runner_api_endpoints:
        return None

    if getattr(g, "authenticated_username", None):
        return None

    return redirect(
        url_for(
            "auth.login",
            next=request.full_path if request.query_string else request.path,
        )
    )


def current_username():
    username = getattr(g, "authenticated_username", None)
    if not username:
        raise RuntimeError("No authenticated Journeyman user is available.")
    return username


def current_display_name():
    return str(
        getattr(g, "authenticated_display_name", None)
        or current_username()
    )


def current_group_names():
    return frozenset(
        str(group_name).strip()
        for group_name in getattr(g, "authenticated_group_names", ())
        if str(group_name).strip()
    )


def current_user_object_guid():
    return _normalise_guid(
        getattr(g, "authenticated_user_object_guid", None)
    )


def current_group_object_guids():
    return frozenset(
        value
        for value in (
            _normalise_guid(item)
            for item in getattr(g, "authenticated_group_object_guids", ())
        )
        if value
    )


def current_user_is_admin():
    return getattr(g, "authenticated_role", None) == "Administrator"


def _safe_next_url(value):
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("main.index")


def _fallback_hash_path():
    return Path(current_app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"])


def _verify_fallback_admin(password):
    from app.services.fallback_admin import active_fallback_activation

    activation = active_fallback_activation()
    if activation is None:
        return None

    path = _fallback_hash_path()
    try:
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        # 0640 is expected. Reject group-writable, executable, or any
        # world-accessible fallback credential file.
        if file_stat.st_mode & 0o027:
            current_app.logger.error(
                "Fallback administrator hash file has unsafe permissions: %s",
                path,
            )
            from app.security_logging import record_security_rejection
            record_security_rejection(
                "fallback_admin_control_failure",
                status_code=401,
                reason="unsafe_fallback_hash_permissions",
            )
            return None
        stored_hash = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not stored_hash:
        return None

    try:
        if check_password_hash(stored_hash, password):
            return activation
        return None
    except (ValueError, TypeError):
        return None


def _store_identity(identity, *, absolute_expires_at=None):
    from app import db
    from app.models import AuthSession

    now = _utc_now()
    absolute_seconds = int(
        current_app.config.get("AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS", 86400)
    )
    if absolute_seconds <= 0:
        raise RuntimeError("AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS must be positive.")

    session_id = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=absolute_seconds)
    if absolute_expires_at is not None:
        requested_expiry = _as_utc(absolute_expires_at)
        if requested_expiry <= now:
            raise RuntimeError("Authentication session expiry must be in the future.")
        expires_at = min(expires_at, requested_expiry)

    row = AuthSession(
        session_id=session_id,
        username=identity["username"],
        user_object_guid=_normalise_guid(identity.get("user_object_guid")),
        created_at=now,
        last_seen_at=now,
        directory_checked_at=(
            now if identity.get("authenticated_via", "ldap") == "ldap" else None
        ),
        expires_at=expires_at,
    )
    db.session.add(row)
    db.session.commit()

    session.clear()
    session["journeyman_session_id"] = session_id
    session["journeyman_identity"] = {
        "username": identity["username"],
        "display_name": identity.get("display_name") or identity["username"],
        "role": identity["role"],
        "user_object_guid": identity.get("user_object_guid"),
        "group_names": list(identity.get("group_names", ())),
        "group_object_guids": list(identity.get("group_object_guids", ())),
        "authenticated_via": identity.get("authenticated_via", "ldap"),
    }
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    if getattr(g, "authenticated_username", None):
        return redirect(_safe_next_url(request.args.get("next")))

    next_url = _safe_next_url(
        request.form.get("next") or request.args.get("next")
    )

    if request.method == "GET":
        return render_template("login.html", next_url=next_url)

    username = str(request.form.get("username") or "").strip()
    password = str(request.form.get("password") or "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return render_template("login.html", next_url=next_url), 400

    fallback_username = current_app.config["FALLBACK_ADMIN_USERNAME"]

    if _login_rate_limited(username):
        return _rate_limited_login_response(username, next_url)

    if hmac.compare_digest(username.casefold(), fallback_username.casefold()):
        fallback_activation = _verify_fallback_admin(password)
        if fallback_activation is None:
            _record_login_failure(username)
            current_app.logger.warning(
                "Failed fallback administrator login for %s from %s",
                username,
                request.remote_addr,
            )
            record_audit_event(
                "authentication.login",
                result="failure",
                actor_username=username,
                authenticated_via="fallback",
                details={"reason": "invalid_credentials"},
            )
            flash("Invalid username or password.", "error")
            return render_template("login.html", next_url=next_url), 401

        _clear_login_failures(username)
        _store_identity(
            {
                "username": fallback_username,
                "display_name": "Fallback Administrator",
                "role": "Administrator",
                "authenticated_via": "fallback",
            },
            absolute_expires_at=fallback_activation.expires_at,
        )
        current_app.logger.critical(
            "Fallback administrator login succeeded from %s",
            request.remote_addr,
        )
        record_audit_event(
            "authentication.login",
            actor_username=fallback_username,
            actor_role="Administrator",
            authenticated_via="fallback",
        )
        return redirect(next_url)

    try:
        from app.services.directory_settings import get_or_create_directory_settings
        from app.services.directory import get_directory_client

        settings = get_or_create_directory_settings()
        authenticated = get_directory_client(settings).authenticate_user(
            username,
            password,
        )
    except Exception as exc:  # Exact directory error is intentionally not exposed.
        _record_login_failure(username)
        current_app.logger.warning(
            "LDAP login failed for %s from %s: %s",
            username,
            request.remote_addr,
            exc,
        )
        record_audit_event(
            "authentication.login",
            result="failure",
            actor_username=username,
            authenticated_via="ldap",
            details={"reason": "authentication_or_directory_failure"},
        )
        flash("Invalid username or password, or directory access is unavailable.", "error")
        return render_template("login.html", next_url=next_url), 401

    _clear_login_failures(username)
    _store_identity(
        {
            "username": authenticated.user.username,
            "display_name": authenticated.user.display_name,
            "role": authenticated.role,
            "user_object_guid": authenticated.user.object_guid,
            "group_names": [group.sam_account_name for group in authenticated.groups],
            "group_object_guids": [group.object_guid for group in authenticated.groups],
            "authenticated_via": "ldap",
        }
    )
    record_audit_event(
        "authentication.login",
        actor_username=authenticated.user.username,
        actor_object_guid=authenticated.user.object_guid,
        actor_role=authenticated.role,
        authenticated_via="ldap",
    )
    return redirect(next_url)


@bp.post("/logout")
def logout():
    session_id = session.get("journeyman_session_id")
    identity = session.get("journeyman_identity")
    authenticated_via = (
        str(identity.get("authenticated_via") or "")
        if isinstance(identity, dict)
        else ""
    )

    if authenticated_via == "fallback":
        from app.services.fallback_admin import expire_fallback_activation
        expire_fallback_activation("logout")
    else:
        _revoke_auth_session(session_id)

    record_audit_event("authentication.logout")
    session.clear()
    return redirect(url_for("auth.login"))


def can_launch_package(
    package,
    *,
    username=None,
    group_names=None,
    user_object_guid=None,
    group_object_guids=None,
    is_admin=None,
):
    if not getattr(package, "enabled", False):
        return False

    project = getattr(package, "project", None)
    if project is None or not getattr(project, "enabled", False):
        return False

    if username is None:
        username = current_username()
    if group_names is None:
        group_names = current_group_names()
    if user_object_guid is None:
        user_object_guid = current_user_object_guid()
    if group_object_guids is None:
        group_object_guids = current_group_object_guids()
    if is_admin is None:
        is_admin = current_user_is_admin()

    if is_admin:
        return True

    access_mode = getattr(package, "access_mode", "")
    if access_mode == "authenticated":
        return True
    if access_mode != "restricted":
        return False

    username_key = str(username or "").strip().casefold()
    group_keys = {
        str(group_name).strip().casefold()
        for group_name in group_names
        if str(group_name).strip()
    }
    user_guid_key = str(user_object_guid or "").strip().lower()
    group_guid_keys = {
        str(object_guid).strip().lower()
        for object_guid in group_object_guids
        if str(object_guid).strip()
    }

    for permission in getattr(package, "permissions", ()):
        principal_type = getattr(permission, "principal_type", "")
        principal_name = str(getattr(permission, "principal_name", "") or "").strip().casefold()
        principal_guid = str(getattr(permission, "principal_object_guid", "") or "").strip().lower()

        if principal_type == "user" and principal_guid and user_guid_key and principal_guid == user_guid_key:
            return True
        if principal_type == "group" and principal_guid and principal_guid in group_guid_keys:
            return True
        if not principal_guid and principal_type == "user" and principal_name == username_key:
            return True
        if not principal_guid and principal_type == "group" and principal_name in group_keys:
            return True

    return False


def can_administer(resource):
    return current_user_is_admin() or getattr(resource, "owner", None) == current_username()


def can_view_job(job):
    return current_user_is_admin() or job.requested_by == current_username()


def can_cancel_job(job):
    return current_user_is_admin() or job.requested_by == current_username()
