"""Journeyman administrative CLI commands."""

import grp
import os
import secrets
import tempfile
from pathlib import Path

import click
from flask import current_app
from werkzeug.security import generate_password_hash


def _write_hash_file(path, password):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}.".format(path.name),
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    password_hash = generate_password_hash(password, method="scrypt")

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(password_hash + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            group_id = grp.getgrnam("journeyman").gr_gid
            os.chown(str(temporary_path), 0, group_id)
        except (KeyError, PermissionError):
            pass
        os.chmod(str(temporary_path), 0o640)
        os.replace(str(temporary_path), str(path))
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def register_cli_commands(app):
    @app.cli.group("fallback-admin")
    def fallback_admin():
        """Manage the local break-glass administrator."""

    @fallback_admin.command("generate")
    @click.option(
        "--length",
        type=click.IntRange(24, 128),
        default=40,
        show_default=True,
        help="Number of random bytes used before URL-safe encoding.",
    )
    @click.option(
        "--lifetime-minutes",
        type=click.IntRange(min=1),
        default=None,
        help="Override the configured break-glass activation lifetime.",
    )
    @click.option(
        "--no-expiry",
        is_flag=True,
        help="Disable automatic activation expiry (strongly discouraged).",
    )
    def generate_fallback_admin(length, lifetime_minutes, no_expiry):
        """Generate a new fallback password and store only its hash."""

        from app.services.fallback_admin import (
            fallback_admin_lifetime_minutes,
            provision_fallback_activation,
        )

        if no_expiry and lifetime_minutes is not None:
            raise click.UsageError(
                "--no-expiry cannot be combined with --lifetime-minutes."
            )
        requested_lifetime = 0 if no_expiry else lifetime_minutes
        try:
            effective_lifetime = fallback_admin_lifetime_minutes(requested_lifetime)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        password = secrets.token_urlsafe(length)
        path = current_app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"]
        _write_hash_file(path, password)
        activation = provision_fallback_activation(
            lifetime_minutes=effective_lifetime
        )

        click.echo("Fallback administrator username: {}".format(
            current_app.config["FALLBACK_ADMIN_USERNAME"]
        ))
        click.echo("Fallback administrator password (shown once): {}".format(password))
        click.echo("Password hash written to: {}".format(path))
        if effective_lifetime == 0:
            click.echo("Break-glass access expiry: none")
            click.echo("Maximum lifetime: no automatic expiry")
            click.echo(
                "WARNING: Non-expiring break-glass access is strongly discouraged "
                "for production deployments."
            )
        else:
            expires_at = activation.expires_at
            if expires_at.tzinfo is None:
                from datetime import timezone
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            click.echo("Break-glass access expires at: {}".format(
                expires_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            ))
            click.echo("Maximum lifetime: {} minutes".format(effective_lifetime))
        click.echo("Signing out expires this activation immediately.")

# Scheduler commands are registered separately to keep the existing
# fallback-admin command unchanged.
def register_scheduler_cli_commands(app):
    @app.cli.command("run-scheduler")
    @click.option("--once", is_flag=True, help="Process due schedules once and exit.")
    @click.option("--poll-seconds", type=click.IntRange(5, 3600), default=30, show_default=True)
    def run_scheduler(once, poll_seconds):
        """Run the Journeyman Project scheduler worker."""
        import signal
        import time
        from app.services.runner_recovery import recover_lost_runner_jobs
        from app.services.job_cancellation import recover_stale_cancelling_jobs
        from app.services.schedules import run_due_schedules
        from app.services.data_retention import purge_expired_protected_data
        from app.services.notifications import process_pending_notifications
        from app.services.fallback_admin import expire_fallback_activation_if_due
        from app.services.runner_runtime_dependencies import refresh_runner_runtime_dependency_audits
        from app.services.runner_transport import refresh_remote_runner_health
        from app.services.runner_certificate_renewal import maintain_runner_certificates

        signal.signal(
            signal.SIGHUP,
            lambda signum, frame: click.echo(
                "Scheduler reload requested; no scheduler-local session material requires reload."
            ),
        )

        next_retention_purge_at = 0.0
        next_runner_dependency_audit_at = 0.0
        next_runner_health_poll_at = 0.0
        next_runner_pki_maintenance_at = 0.0
        while True:
            expire_fallback_activation_if_due()
            recovery = recover_lost_runner_jobs()
            stale_cancellations = recover_stale_cancelling_jobs()
            if stale_cancellations:
                click.echo(
                    "Cancellation recovery: cancelled={}".format(
                        len(stale_cancellations)
                    )
                )
            if any(recovery.values()):
                click.echo(
                    "Lost-runner recovery: requeued={requeued}, "
                    "failed={failed}, cancelled={cancelled}, "
                    "slices_failed={slices_failed}, "
                    "slices_cancelled={slices_cancelled}".format(
                        requeued=len(recovery["requeued"]),
                        failed=len(recovery["failed"]),
                        cancelled=len(recovery["cancelled"]),
                        slices_failed=len(recovery["slices_failed"]),
                        slices_cancelled=len(recovery["slices_cancelled"]),
                    )
                )
            now_monotonic = time.monotonic()
            if now_monotonic >= next_runner_pki_maintenance_at:
                try:
                    pki_result = maintain_runner_certificates()
                except Exception as exc:
                    click.echo("Runner PKI maintenance failed: {}".format(exc), err=True)
                else:
                    if (
                        pki_result["ca_renewed"]
                        or pki_result["controller_renewed"]
                        or pki_result["runner_renewed"]
                        or pki_result["runner_failed"]
                    ):
                        click.echo(
                            "Runner PKI maintenance: ca_renewed={} controller_renewed={} "
                            "runners_renewed={} runners_failed={}".format(
                                pki_result["ca_renewed"],
                                pki_result["controller_renewed"],
                                len(pki_result["runner_renewed"]),
                                len(pki_result["runner_failed"]),
                            )
                        )
                next_runner_pki_maintenance_at = now_monotonic + 3600
            if now_monotonic >= next_runner_health_poll_at:
                try:
                    health_result = refresh_remote_runner_health()
                except Exception as exc:
                    click.echo("Runner mTLS health poll failed: {}".format(exc), err=True)
                else:
                    if health_result["failed"]:
                        click.echo(
                            "Runner mTLS health poll: updated={} failed={}".format(
                                health_result["updated"], len(health_result["failed"])
                            )
                        )
                next_runner_health_poll_at = now_monotonic + 30
            if now_monotonic >= next_retention_purge_at:
                purged = purge_expired_protected_data()
                if (
                    purged["job_ids"]
                    or purged["reaction_ids"]
                    or purged["inventory_cache_paths"]
                ):
                    click.echo(
                        "Data retention purge: jobs={} reactions={} "
                        "inventory_caches={}".format(
                            len(purged["job_ids"]),
                            len(purged["reaction_ids"]),
                            len(purged["inventory_cache_paths"]),
                        )
                    )
                next_retention_purge_at = now_monotonic + max(
                    60,
                    int(current_app.config.get(
                        "DATA_RETENTION_PURGE_INTERVAL_SECONDS", 3600
                    )),
                )

            if now_monotonic >= next_runner_dependency_audit_at:
                audit_result = refresh_runner_runtime_dependency_audits()
                if audit_result["audited"]:
                    click.echo(
                        "Runner runtime dependency audit: audited={} clean={} "
                        "findings={} errors={}".format(
                            len(audit_result["audited"]),
                            audit_result["clean"],
                            audit_result["findings"],
                            audit_result["errors"],
                        )
                    )
                next_runner_dependency_audit_at = now_monotonic + max(
                    60,
                    int(current_app.config.get(
                        "RUNNER_RUNTIME_AUDIT_SCAN_INTERVAL_SECONDS", 300
                    )),
                )

            notification_result = process_pending_notifications()
            if notification_result["sent"] or notification_result["failed"]:
                click.echo("Notifications: sent={} failed={}".format(
                    notification_result["sent"], notification_result["failed"]
                ))

            jobs = run_due_schedules()
            if jobs:
                click.echo("Queued scheduled Jobs: {}".format(
                    ", ".join(str(job.id) for job in jobs)
                ))
            if once:
                return
            time.sleep(poll_seconds)

    @app.cli.command("purge-retained-data")
    @click.option("--dry-run", is_flag=True, help="Report what would be purged without deleting it.")
    def purge_retained_data(dry_run):
        """Apply configured Job, Reaction and inventory-cache retention policies."""
        from app.services.data_retention import purge_expired_protected_data
        result = purge_expired_protected_data(dry_run=dry_run)
        click.echo(
            "{} jobs={} reactions={} inventory_caches={}".format(
                "Would purge" if dry_run else "Purged",
                len(result["job_ids"]),
                len(result["reaction_ids"]),
                len(result["inventory_cache_paths"]),
            )
        )



    @app.cli.command("audit-runner-runtime-dependencies")
    @click.option("--force", is_flag=True, help="Re-audit even when the dependency set has a fresh cached result.")
    def audit_runner_runtime_dependencies(force):
        """Audit Journeyman runner-runtime dependency sets with central pip-audit."""
        from app.services.runner_runtime_dependencies import refresh_runner_runtime_dependency_audits
        result = refresh_runner_runtime_dependency_audits(force=force)
        click.echo(
            "Runner runtime dependency audit: audited={} clean={} findings={} errors={}".format(
                len(result["audited"]),
                result["clean"],
                result["findings"],
                result["errors"],
            )
        )

def register_credential_key_cli_commands(app):
    @app.cli.group("credential-key")
    def credential_key():
        """Manage Journeyman at-rest credential-encryption keys."""

    def _audit(action, *, result="success", details=None):
        from app.services.audit import record_audit_event
        record_audit_event(
            action,
            result=result,
            object_type="credential_storage_key",
            details=details or {},
            actor_username="system",
            authenticated_via="cli",
        )

    @credential_key.command("generate")
    @click.option("--key-id", required=True, help="New storage-key identifier, for example storage-2026-09.")
    @click.option("--key-size", type=click.IntRange(min=2048), default=3072, show_default=True)
    def generate_credential_key(key_id, key_size):
        """Generate a new RSA storage keypair in decrypt-only state."""
        from app.services.credential_key_lifecycle import generate_storage_key
        try:
            metadata = generate_storage_key(key_id, key_size=key_size)
        except Exception as exc:
            _audit("credential_key.generate", result="failed", details={"key_id": key_id})
            raise click.ClickException(str(exc)) from exc
        _audit("credential_key.generate", details={"key_id": metadata["key_id"], "key_size": metadata["key_size"]})
        click.echo("Generated credential storage key: {}".format(metadata["key_id"]))
        click.echo("State: decrypt-only")

    @credential_key.command("status")
    def credential_key_status():
        """Show the active RSA storage key and retained decrypt-only keys."""
        from app.services.credential_key_lifecycle import storage_key_status
        status = storage_key_status()
        click.echo("Active credential storage key: {}".format(status["active_key_id"] or "legacy/default"))
        if not status["keys"]:
            click.echo("No RSA credential storage keys found.")
            return
        for item in status["keys"]:
            click.echo(
                "{}  state={}  bits={}  private={}  created={}".format(
                    item["key_id"],
                    item["state"],
                    item["key_size"] or "unknown",
                    "yes" if item["private_key_present"] else "NO",
                    item["created_at"] or "unknown",
                )
            )

    @credential_key.command("activate")
    @click.option("--key-id", required=True, help="Existing RSA storage-key identifier.")
    def activate_credential_key(key_id):
        """Make an existing RSA key active for newly encrypted secrets."""
        from app.services.credential_key_lifecycle import activate_storage_key
        try:
            previous = activate_storage_key(key_id)
        except Exception as exc:
            _audit("credential_key.activate", result="failed", details={"key_id": key_id})
            raise click.ClickException(str(exc)) from exc
        _audit("credential_key.activate", details={"key_id": key_id, "previous_key_id": previous})
        click.echo("Active credential storage key: {}".format(key_id))
        if previous and previous != key_id:
            click.echo("Previous key is retained as decrypt-only: {}".format(previous))

    @credential_key.command("rotate")
    @click.option("--key-id", required=True, help="Target RSA storage-key identifier.")
    @click.option("--generate", is_flag=True, help="Generate the target RSA keypair before rotation.")
    @click.option("--key-size", type=click.IntRange(min=2048), default=3072, show_default=True)
    def rotate_credential_key(key_id, generate, key_size):
        """Rewrap all v2 data keys to a new RSA storage key and activate it."""
        from app import db
        from app.services.credential_key_lifecycle import (
            activate_storage_key,
            generate_storage_key,
            rewrap_v2_rows,
        )
        try:
            if generate:
                generate_storage_key(key_id, key_size=key_size)
            counts = rewrap_v2_rows(key_id)
            db.session.commit()
            previous = activate_storage_key(key_id)
        except Exception as exc:
            db.session.rollback()
            _audit("credential_key.rotate", result="failed", details={"key_id": key_id})
            raise click.ClickException(str(exc)) from exc
        _audit(
            "credential_key.rotate",
            details={
                "key_id": key_id,
                "previous_key_id": previous,
                "rewrapped": counts["rewrapped"],
                "already_current": counts["already_current"],
                "legacy_skipped": counts["legacy_skipped"],
            },
        )
        click.echo("Credential storage-key rotation complete: {}".format(key_id))
        click.echo(
            "Rewrapped {} v2 payloads; {} already used the target key; {} legacy payloads were left unchanged.".format(
                counts["rewrapped"], counts["already_current"], counts["legacy_skipped"]
            )
        )
        if previous and previous != key_id:
            click.echo("Previous key is retained as decrypt-only: {}".format(previous))

    @credential_key.command("migrate-legacy")
    def migrate_legacy_credential_data():
        """Convert all remaining Fernet payloads to v2 envelope encryption."""
        from app import db
        from app.services.credential_key_lifecycle import migrate_legacy_rows
        try:
            counts = migrate_legacy_rows()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _audit("credential_key.migrate_legacy", result="failed")
            raise click.ClickException(str(exc)) from exc
        _audit("credential_key.migrate_legacy", details=counts)
        click.echo(
            "Legacy credential migration complete: migrated={} v2_skipped={}.".format(
                counts["migrated"], counts["v2_skipped"]
            )
        )
        click.echo("Retain legacy Fernet keys until backup and rollback retention requirements have expired.")


def register_runner_pki_cli_commands(app):
    @app.cli.group("runner-pki")
    def runner_pki():
        """Manage the Journeyman runner certificate authority."""

    def _audit(action, *, result="success", details=None):
        from app.services.audit import record_audit_event
        record_audit_event(
            action,
            result=result,
            object_type="runner_pki",
            details=details or {},
            actor_username="system",
            authenticated_via="cli",
        )

    @runner_pki.command("init-ca")
    @click.option("--key-size", type=click.IntRange(min=3072), default=4096, show_default=True)
    def init_runner_ca(key_size):
        """Create the Journeyman-managed runner CA."""
        from app.services.runner_pki import generate_runner_ca
        try:
            metadata = generate_runner_ca(key_size=key_size)
        except Exception as exc:
            _audit("runner_pki.init_ca", result="failed")
            raise click.ClickException(str(exc)) from exc
        _audit(
            "runner_pki.init_ca",
            details={
                "serial": metadata["serial"],
                "fingerprint_sha256": metadata["fingerprint_sha256"],
            },
        )
        click.echo("Runner CA initialized.")
        click.echo("Fingerprint (SHA-256): {}".format(metadata["fingerprint_sha256"]))
        click.echo("Valid until: {}".format(metadata["not_after"]))

    @runner_pki.command("status")
    def runner_pki_status():
        """Show runner CA status and renewal state."""
        from app.services.runner_pki import runner_ca_status
        try:
            status = runner_ca_status()
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc
        if not status["initialized"]:
            click.echo("Runner CA: not initialized")
            return
        click.echo("Runner CA: initialized")
        click.echo("Subject: {}".format(status["subject"]))
        click.echo("Fingerprint (SHA-256): {}".format(status["fingerprint_sha256"]))
        click.echo("RSA bits: {}".format(status["key_size"]))
        click.echo("Valid from: {}".format(status["not_before"].isoformat()))
        click.echo("Valid until: {}".format(status["not_after"].isoformat()))
        click.echo("Renew after: {}".format(status["renew_after"].isoformat()))
        click.echo("Renewal due: {}".format("yes" if status["renewal_due"] else "no"))
        click.echo("Expired: {}".format("yes" if status["expired"] else "no"))

    @runner_pki.command("renew-ca")
    @click.option(
        "--force",
        is_flag=True,
        help="Renew before the normal 39-week renewal point.",
    )
    def renew_runner_ca(force):
        """Renew the runner CA certificate while retaining its private key."""
        from app.services.runner_pki import renew_runner_ca_certificate
        try:
            metadata = renew_runner_ca_certificate(force=force)
        except Exception as exc:
            _audit("runner_pki.renew_ca", result="failed", details={"forced": bool(force)})
            raise click.ClickException(str(exc)) from exc
        _audit(
            "runner_pki.renew_ca",
            details={
                "forced": bool(force),
                "serial": metadata["serial"],
                "fingerprint_sha256": metadata["fingerprint_sha256"],
            },
        )
        click.echo("Runner CA certificate renewed; CA private key retained.")
        click.echo("Fingerprint (SHA-256): {}".format(metadata["fingerprint_sha256"]))
        click.echo("Valid until: {}".format(metadata["not_after"]))

    @runner_pki.command("ensure-controller")
    @click.option("--force", is_flag=True, help="Replace the existing controller management identity.")
    def ensure_runner_controller_identity(force):
        """Create the controller mTLS client identity used on TCP/8443."""
        from app.services.runner_pki import ensure_controller_client_identity
        try:
            metadata = ensure_controller_client_identity(force=force)
        except Exception as exc:
            _audit("runner_pki.controller_identity", result="failed", details={"forced": bool(force)})
            raise click.ClickException(str(exc)) from exc
        _audit(
            "runner_pki.controller_identity",
            details={
                "forced": bool(force),
                "created": bool(metadata["created"]),
                "serial": metadata["serial"],
                "fingerprint_sha256": metadata["fingerprint_sha256"],
            },
        )
        click.echo(
            "Runner management controller identity {}.".format(
                "created" if metadata["created"] else "already exists"
            )
        )
        click.echo("Fingerprint (SHA-256): {}".format(metadata["fingerprint_sha256"]))
        click.echo("Valid until: {}".format(metadata["not_after"].isoformat()))


    @runner_pki.command("enable-web-mtls")
    def enable_runner_web_mtls():
        """Re-render Nginx so runner -> controller APIs request client certificates."""
        from pathlib import Path
        from app.services.system_settings import get_or_create_system_settings
        from app.services.system_settings_apply import apply_nginx_settings

        helper = Path(current_app.config["NGINX_APPLY_HELPER"])
        try:
            helper_text = helper.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise click.ClickException(
                "Unable to inspect the installed Nginx apply helper: {}".format(exc)
            ) from exc
        if "ssl_client_certificate" not in helper_text:
            raise click.ClickException(
                "Installed Nginx apply helper predates runner mTLS support. "
                "Install scripts/journeyman-apply-web-settings to {} first.".format(helper)
            )
        try:
            result = apply_nginx_settings(get_or_create_system_settings())
        except Exception as exc:
            _audit("runner_pki.web_mtls", result="failed")
            raise click.ClickException(str(exc)) from exc
        _audit("runner_pki.web_mtls")
        click.echo(result["message"])
        click.echo("Runner client-certificate verification enabled on Journeyman HTTPS.")
