from app import db
from app.models import Runner
from app.services.runner_transport import refresh_remote_runner_health


def test_mtls_health_poll_updates_runner(app, monkeypatch, tmp_path):
    root = tmp_path / "runner-pki"
    root.mkdir()
    for name in ("controller-cert.pem", "controller-key.pem", "ca-cert.pem"):
        (root / name).write_text("placeholder")
    app.config.update(
        RUNNER_CONTROLLER_CERTIFICATE_PATH=str(root / "controller-cert.pem"),
        RUNNER_CONTROLLER_PRIVATE_KEY_PATH=str(root / "controller-key.pem"),
        RUNNER_CA_CERTIFICATE_PATH=str(root / "ca-cert.pem"),
        RUNNER_MANAGEMENT_PORT=8443,
        RUNNER_MANAGEMENT_TIMEOUT_SECONDS=5,
    )

    seen = {}

    def fake_fetch(hostname, management_port, runner_uuid, serial, fingerprint):
        seen.update(hostname=hostname, management_port=management_port, runner_uuid=runner_uuid)
        return {
            "runner_uuid": runner_uuid,
            "hostname": hostname,
            "version": "0.18",
            "status_message": "Ready",
            "running_steps": 0,
            "free_workspace_bytes": 123456,
            "load_average_1m": 0.1,
            "load_average_5m": 0.2,
            "cpu_count": 4,
            "runtime_dependencies": {"cryptography": "46.0.0"},
            "capabilities": ["ansible", "shell"],
            "managed_capabilities": {},
            "environments": [],
        }

    monkeypatch.setattr("app.services.runner_transport.fetch_runner_health", fake_fetch)

    with app.app_context():
        runner = Runner(
            name="mtls-runner",
            hostname="runner01.example.test",
            management_port=9443,
            runner_uuid="11111111-2222-3333-4444-555555555555",
            api_secret_digest="transitional",
            pki_certificate_fingerprint_sha256="a" * 64,
            is_local=False,
            enabled=True,
        )
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

        result = refresh_remote_runner_health()
        db.session.expire_all()
        stored = db.session.get(Runner, runner_id)

        assert result == {"updated": 1, "failed": {}}
        assert seen["management_port"] == 9443
        assert stored.version == "0.18"
        assert stored.status_message == "Ready"
        assert stored.free_workspace_bytes == 123456
        assert stored.last_heartbeat_at is not None


def test_identity_failure_quarantines_runner(app, monkeypatch):
    from app.services.runner_transport import RunnerIdentityError
    from app.services.runners import runner_health

    def fake_fetch(*args, **kwargs):
        raise RunnerIdentityError("Runner certificate SHA-256 fingerprint mismatch.")

    monkeypatch.setattr("app.services.runner_transport.fetch_runner_health", fake_fetch)

    with app.app_context():
        runner = Runner(
            name="bad-identity",
            hostname="runner02.example.test",
            management_port=8443,
            runner_uuid="22222222-3333-4444-5555-666666666666",
            api_secret_digest="transitional",
            pki_certificate_serial="1234",
            pki_certificate_fingerprint_sha256="b" * 64,
            is_local=False,
            enabled=True,
        )
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

        result = refresh_remote_runner_health()
        db.session.expire_all()
        stored = db.session.get(Runner, runner_id)

        assert result["updated"] == 0
        assert runner_id in result["failed"]
        assert stored.pki_quarantined is True
        assert "fingerprint mismatch" in stored.pki_quarantine_reason
        assert stored.pki_quarantined_at is not None
        assert runner_health(stored) == "quarantined"


def test_transport_failure_does_not_quarantine_runner(app, monkeypatch):
    from app.services.runner_transport import RunnerTransportError

    def fake_fetch(*args, **kwargs):
        raise RunnerTransportError("connection refused")

    monkeypatch.setattr("app.services.runner_transport.fetch_runner_health", fake_fetch)

    with app.app_context():
        runner = Runner(
            name="offline-runner",
            hostname="runner03.example.test",
            management_port=8443,
            runner_uuid="33333333-4444-5555-6666-777777777777",
            api_secret_digest="transitional",
            pki_certificate_serial="5678",
            pki_certificate_fingerprint_sha256="c" * 64,
            is_local=False,
            enabled=True,
        )
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

        result = refresh_remote_runner_health()
        db.session.expire_all()
        stored = db.session.get(Runner, runner_id)

        assert runner_id in result["failed"]
        assert stored.pki_quarantined is False
        assert stored.pki_quarantine_reason == ""
