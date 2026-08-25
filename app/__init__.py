import os
import secrets
from pathlib import Path

from flask import Flask, g, render_template, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from journeyman_configuration import load_journeyman_configuration
from flask_wtf.csrf import (
    CSRFError,
    CSRFProtect,
)


db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()


def read_journeyman_version():
    """Return the application version from the repository VERSION file."""

    version_file = (
        Path(__file__).resolve().parents[1]
        / "VERSION"
    )

    try:
        version = version_file.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return "unknown"

    return version or "unknown"


def validate_runtime_directory(path, setting_name):
    """
    Confirm that a configured runtime directory exists and is writable.

    Journeyman deliberately does not create system directories itself.
    Creating /var/lib and /var/log locations is a system administrator
    responsibility.
    """

    directory = Path(path)

    if not directory.exists():
        raise RuntimeError(
            "{} does not exist: {}".format(
                setting_name,
                directory,
            )
        )

    if not directory.is_dir():
        raise RuntimeError(
            "{} is not a directory: {}".format(
                setting_name,
                directory,
            )
        )

    if not os.access(str(directory), os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(
            "{} is not accessible for reading and writing: {}".format(
                setting_name,
                directory,
            )
        )


def create_app(config_object=None, *, instance_path=None):
    flask_kwargs = {
        "instance_relative_config": True,
    }
    if instance_path is not None:
        flask_kwargs["instance_path"] = str(instance_path)

    app = Flask(
        __name__,
        **flask_kwargs,
    )

    from app.security_session import BoundedSecureCookieSessionInterface
    app.session_interface = BoundedSecureCookieSessionInterface()

    if config_object is None:
        load_journeyman_configuration()

    config_name = (
        config_object
        or os.environ.get(
            "JOURNEYMAN_CONFIG_CLASS",
            "app.config.DevelopmentConfig",
        )
    )

    app.config.from_object(
        config_name
    )

    if app.config.get("OUTBOUND_SECURE_TRANSPORT_ENFORCED", False):
        from app.services.outbound_security import validate_database_transport
        validate_database_transport(app.config.get("SQLALCHEMY_DATABASE_URI"))

    if not app.config.get("DEBUG", False):
        secret_key = str(app.config.get("SECRET_KEY") or "").strip()
        unsafe_secret_keys = {
            "",
            "development-only-change-me",
            "CHANGE_ME",
        }
        if secret_key in unsafe_secret_keys:
            raise RuntimeError(
                "Production Journeyman requires a managed session-signing key; "
                "run journeyman-service-coordinator prepare before startup."
            )

    app.config["JOURNEYMAN_VERSION"] = (
        read_journeyman_version()
    )

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=int(
            app.config.get(
                "PROXY_FIX_X_FOR",
                0,
            )
        ),
        x_proto=int(
            app.config.get(
                "PROXY_FIX_X_PROTO",
                0,
            )
        ),
        x_host=int(
            app.config.get(
                "PROXY_FIX_X_HOST",
                0,
            )
        ),
        x_port=int(
            app.config.get(
                "PROXY_FIX_X_PORT",
                0,
            )
        ),
    )

    Path(app.instance_path).mkdir(
        parents=True,
        exist_ok=True,
    )

    validate_runtime_directory(
        app.config["REPOSITORY_ROOT"],
        "REPOSITORY_ROOT",
    )

    validate_runtime_directory(
        app.config["LOG_ROOT"],
        "LOG_ROOT",
    )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    from app.security_logging import (
        audit_rejected_response,
        install_log_injection_filters,
        record_security_rejection,
    )
    install_log_injection_filters(app)
    app.after_request(audit_rejected_response)

    # Register canonical Job/Step notification event listeners only after the
    # model and audit modules are fully initialized. Importing notification
    # services from app.models creates an audit -> models -> notifications ->
    # audit circular import during application startup.
    from app.services import notifications as _notification_event_listeners  # noqa: F401

    @app.before_request
    def prepare_content_security_policy_nonce():
        g.csp_nonce = secrets.token_urlsafe(24)

    @app.context_processor
    def inject_content_security_policy_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def apply_browser_security_headers(response):
        """Apply browser-side security controls to every application response."""

        # Some request handlers, including CSRF rejection, can terminate before
        # normal before_request processing has established the CSP nonce.
        # Security headers must still be valid on those early/error responses.
        csp_nonce = getattr(g, "csp_nonce", None)
        if not csp_nonce:
            csp_nonce = secrets.token_urlsafe(24)
            g.csp_nonce = csp_nonce

        response.headers.setdefault(
            "X-Content-Type-Options",
            "nosniff",
        )
        response.headers.setdefault(
            "Referrer-Policy",
            "same-origin",
        )
        response.headers.setdefault(
            "Cross-Origin-Opener-Policy",
            "same-origin",
        )
        response.headers.setdefault(
            "Cross-Origin-Resource-Policy",
            "same-origin",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join(
                (
                    "default-src 'self'",
                    "base-uri 'none'",
                    "object-src 'none'",
                    "frame-ancestors 'none'",
                    "form-action 'self'",
                    "img-src 'self' data:",
                    "style-src 'self' 'nonce-{}'".format(csp_nonce),
                    "script-src 'self' 'nonce-{}'".format(csp_nonce),
                    "connect-src 'self'",
                )
            ),
        )

        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        # Authenticated Journeyman pages can contain protected operational
        # data. Do not allow browsers or intermediary caches to retain them.
        if getattr(g, "authenticated_username", None):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, private, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        from app.services.audit import record_audit_event

        record_audit_event(
            "security.csrf_rejected",
            result="failure",
            details={
                "method": request.method,
                "path": request.path,
            },
        )
        return (
            render_template(
                "csrf_error.html",
                reason=error.description,
            ),
            400,
        )

    @app.errorhandler(403)
    def handle_forbidden(error):
        from app.services.audit import record_audit_event

        record_audit_event(
            "authorization.denied",
            result="failure",
            details={
                "method": request.method,
                "path": request.path,
            },
        )
        return (
            render_template(
                "error.html",
                status_code=403,
                message="You are not authorized to perform this action.",
            ),
            403,
        )

    @app.errorhandler(500)
    def handle_internal_server_error(error):
        # Database exceptions leave SQLAlchemy's request-scoped Session in a
        # failed transaction.  Recover it before security/audit logging tries
        # to persist a second record, otherwise the useful original exception
        # is obscured by PendingRollbackError noise.
        db.session.rollback()
        record_security_rejection(
            "unhandled_exception",
            status_code=500,
            reason="application_exception",
        )
        original = getattr(error, "original_exception", None)
        if original is not None:
            app.logger.error(
                "Unhandled application exception",
                exc_info=(
                    type(original),
                    original,
                    original.__traceback__,
                ),
            )
        else:
            app.logger.error("Internal server error: %s", error)

        return (
            render_template(
                "error.html",
                status_code=500,
                message="An unexpected error occurred.",
            ),
            500,
        )

    from app import models  # noqa: F401
    from app.auth import (
        authentication_required,
        bp as auth_bp,
        load_authenticated_user,
    )
    from app.routes import bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(bp)

    from app.api import bp as api_v1_bp
    csrf.exempt(api_v1_bp)
    app.register_blueprint(api_v1_bp)

    from app.request_parameters import reject_ambiguous_request_parameters

    app.before_request(reject_ambiguous_request_parameters)
    app.before_request(load_authenticated_user)
    app.before_request(authentication_required)

    from app.auth import (
        current_display_name,
        current_user_is_admin,
        current_username,
    )

    @app.context_processor
    def inject_journeyman_version():
        return {
            "journeyman_version": app.config[
                "JOURNEYMAN_VERSION"
            ],
        }

    @app.context_processor
    def inject_current_identity():
        # Flask-WTF can reject an invalid CSRF request before the
        # normal authentication before_request hook runs. The CSRF
        # error page extends the identity-aware base template, so make
        # sure a safe identity context exists for early error rendering.
        if not getattr(g, "authenticated_username", None):
            return {
                "journeyman_username": "",
                "journeyman_display_name": "",
                "journeyman_is_admin": False,
                "journeyman_initials": "",
                "journeyman_break_glass": False,
                "journeyman_break_glass_activated_at": "",
                "journeyman_break_glass_expires_at": "",
                "journeyman_security_notices": [],
                "journeyman_runtime_dependencies": (),
                "journeyman_runtime_python_version": "",
            }

        username = current_username()
        display_name = current_display_name()

        name_parts = [
            part.strip(" ,.;:-")
            for part in display_name.split()
            if part.strip(" ,.;:-")
        ]

        initials = (
            "".join(
                part[:1].upper()
                for part in name_parts[:2]
            )
            or username[:2].upper()
        )

        from app.services.secret_lifecycle import security_notices_for_identity
        is_admin = current_user_is_admin()

        runtime_dependencies = ()
        runtime_python_version = ""
        if is_admin:
            from app.services.about import (
                runtime_dependency_inventory,
                runtime_python_version as read_runtime_python_version,
            )
            runtime_dependencies = runtime_dependency_inventory()
            runtime_python_version = read_runtime_python_version()

        return {
            "journeyman_username": username,
            "journeyman_display_name": display_name,
            "journeyman_is_admin": is_admin,
            "journeyman_security_notices": security_notices_for_identity(
                username, is_admin=is_admin
            ),
            "journeyman_runtime_dependencies": runtime_dependencies,
            "journeyman_runtime_python_version": runtime_python_version,
            "journeyman_initials": initials,
            "journeyman_break_glass": getattr(g, "authenticated_via", None) == "fallback",
            "journeyman_break_glass_activated_at": (
                getattr(g, "break_glass_activated_at", None).isoformat()
                if getattr(g, "break_glass_activated_at", None) else ""
            ),
            "journeyman_break_glass_expires_at": (
                getattr(g, "break_glass_expires_at", None).isoformat()
                if getattr(g, "break_glass_expires_at", None) else ""
            ),
        }

    from app.cli import register_cli_commands, register_scheduler_cli_commands, register_credential_key_cli_commands
    register_cli_commands(app)
    register_scheduler_cli_commands(app)
    register_credential_key_cli_commands(app)

    return app
