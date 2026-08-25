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
    def generate_fallback_admin(length):
        """Generate a new fallback password and store only its hash."""

        from app.services.fallback_admin import provision_fallback_activation

        password = secrets.token_urlsafe(length)
        path = current_app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"]
        _write_hash_file(path, password)
        activation = provision_fallback_activation()

        click.echo("Fallback administrator username: {}".format(
            current_app.config["FALLBACK_ADMIN_USERNAME"]
        ))
        click.echo("Fallback administrator password (shown once): {}".format(password))
        click.echo("Password hash written to: {}".format(path))
        expires_at = activation.expires_at
        if expires_at.tzinfo is None:
            from datetime import timezone
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        click.echo("Break-glass access expires at: {}".format(
            expires_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        ))
        click.echo("Maximum lifetime: 60 minutes")
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

        signal.signal(
            signal.SIGHUP,
            lambda signum, frame: click.echo(
                "Scheduler reload requested; no scheduler-local session material requires reload."
            ),
        )

        next_retention_purge_at = 0.0
        next_runner_dependency_audit_at = 0.0
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
        """Manage versioned credential-encryption keys."""

    @credential_key.command("rotate")
    @click.option("--key-id", required=True, help="New key identifier, for example 2026-08.")
    @click.option("--generate", is_flag=True, help="Generate and install the new key before rotation.")
    def rotate_credential_key(key_id, generate):
        """Re-encrypt all stored credentials and snapshots with a new key."""
        from cryptography.fernet import Fernet
        from app import db
        from app.credential_crypto import (
            _validate_key_id, credential_active_key_file, credential_keyring_dir,
            decrypt_credential_data, encrypt_credential_data_with_key_id,
        )
        from app.models import Credential, JobCredentialSnapshot

        key_id = _validate_key_id(key_id)
        keyring = credential_keyring_dir()
        keyring.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(keyring, 0o700)
        new_key_path = keyring / (key_id + ".key")

        if generate:
            if new_key_path.exists():
                raise click.ClickException("Key file already exists: {}".format(new_key_path))
            fd = os.open(str(new_key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key() + b"\\n")
        elif not new_key_path.exists():
            raise click.ClickException("New key file does not exist: {}".format(new_key_path))

        active_path = credential_active_key_file()
        old_active = active_path.read_text(encoding="utf-8").strip() if active_path.exists() else None

        # Make the new key active only for this transaction's encryption calls.
        temporary_active = active_path.with_name(active_path.name + ".tmp")
        temporary_active.write_text(key_id + "\\n", encoding="utf-8")
        os.chmod(temporary_active, 0o600)
        os.replace(temporary_active, active_path)

        try:
            credentials = db.session.execute(db.select(Credential)).scalars().all()
            snapshots = db.session.execute(db.select(JobCredentialSnapshot)).scalars().all()
            for item in credentials + snapshots:
                if item.encrypted_data is None:
                    continue
                plaintext = decrypt_credential_data(item.encrypted_data, item.credential_key_id)
                item.encrypted_data, item.credential_key_id = encrypt_credential_data_with_key_id(plaintext)
            db.session.commit()
        except Exception:
            db.session.rollback()
            if old_active:
                active_path.write_text(old_active + "\\n", encoding="utf-8")
                os.chmod(active_path, 0o600)
            else:
                try:
                    active_path.unlink()
                except FileNotFoundError:
                    pass
            raise

        click.echo("Credential key rotation complete: {}".format(key_id))
        click.echo("Next rotation is due within 12 months; Journeyman warns administrators from 30 days before due.")
        click.echo("Re-encrypted {} live credentials and {} job snapshots.".format(len(credentials), len(snapshots)))
        click.echo("Retain previous key files until backup retention and rollback requirements have expired.")
