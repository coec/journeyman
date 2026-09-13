from datetime import datetime, timedelta, timezone

from app import db
from app.models import Runner
from app.services.runner_certificate_renewal import maintain_runner_certificates


def _runner(now):
    return Runner(
        name="renew-runner",
        runner_uuid="11111111-2222-3333-4444-555555555555",
        hostname="runner.example.test",
        enabled=True,
        is_local=False,
        management_port=8443,
        pki_certificate_serial="oldserial",
        pki_certificate_fingerprint_sha256="a" * 64,
        pki_certificate_not_after_at=now + timedelta(days=10),
        pki_certificate_issued_at=now - timedelta(days=20),
    )


def _configure(app):
    app.config.update(
        RUNNER_CERTIFICATE_RENEW_BEFORE_DAYS=15,
        RUNNER_CERTIFICATE_RETRY_SECONDS=86400,
        RUNNER_CERTIFICATE_WARNING_FAILURES=3,
    )


def test_due_runner_certificate_is_renewed_without_uuid_change(app, monkeypatch):
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    _configure(app)
    with app.app_context():
        runner = _runner(now)
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.runner_ca_status",
            lambda now=None: {"initialized": True, "renewal_due": False},
        )
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.controller_client_identity_status",
            lambda now=None: {"initialized": True, "renewal_due": False},
        )
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.fetch_runner_renewal_csr",
            lambda runner: "CSR",
        )
        issued = {
            "certificate_pem": "CERT",
            "ca_certificate_pem": "CA",
            "serial": "newserial",
            "fingerprint_sha256": "b" * 64,
            "not_before": now - timedelta(minutes=5),
            "not_after": now + timedelta(days=30),
        }
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.sign_runner_csr",
            lambda *args, **kwargs: issued,
        )
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.install_runner_renewed_certificate",
            lambda *args, **kwargs: {
                "runner_uuid": runner.runner_uuid,
                "serial": "newserial",
                "fingerprint_sha256": "b" * 64,
            },
        )

        result = maintain_runner_certificates(now=now)
        stored = db.session.get(Runner, runner_id)
        assert result["runner_renewed"] == [runner_id]
        assert stored.runner_uuid == "11111111-2222-3333-4444-555555555555"
        assert stored.pki_certificate_serial == "newserial"
        assert stored.pki_certificate_fingerprint_sha256 == "b" * 64
        assert stored.pki_renewal_failure_count == 0
        assert stored.pki_renewal_last_error == ""


def test_failed_renewal_retries_daily_and_warns_after_three(app, monkeypatch):
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    _configure(app)
    with app.app_context():
        runner = _runner(now)
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.runner_ca_status",
            lambda now=None: {"initialized": True, "renewal_due": False},
        )
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.controller_client_identity_status",
            lambda now=None: {"initialized": True, "renewal_due": False},
        )
        monkeypatch.setattr(
            "app.services.runner_certificate_renewal.fetch_runner_renewal_csr",
            lambda runner: (_ for _ in ()).throw(RuntimeError("runner unavailable")),
        )

        first = maintain_runner_certificates(now=now)
        assert runner_id in first["runner_failed"]
        stored = db.session.get(Runner, runner_id)
        assert stored.pki_renewal_failure_count == 1

        skipped = maintain_runner_certificates(now=now + timedelta(hours=23))
        assert skipped["runner_failed"] == {}
        assert db.session.get(Runner, runner_id).pki_renewal_failure_count == 1

        maintain_runner_certificates(now=now + timedelta(days=1, minutes=1))
        third = maintain_runner_certificates(now=now + timedelta(days=2, minutes=2))
        stored = db.session.get(Runner, runner_id)
        assert runner_id in third["runner_failed"]
        assert stored.pki_renewal_failure_count == 3
        assert stored.pki_renewal_warning_at is not None
        assert "runner unavailable" in stored.pki_renewal_last_error
