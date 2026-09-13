from app import db
from app.models import Runner


def test_resource_admin_can_generate_manual_runner_bootstrap(app, client):
    response = client.post(
        "/runners/manual-bootstrap",
        data={
            "name": "remote-corporate-1",
            "site": "corporate",
            "capabilities": ["ansible", "shell"],
            "max_concurrent_steps": "3",
            "management_port": "8443",
            "https_proxy": "http://proxy.example:3128",
            "no_proxy": "jm.example",
        },
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert response.mimetype == "text/x-shellscript"
    assert "attachment;" in response.headers["Content-Disposition"]
    body = response.data.decode("utf-8")
    assert body.startswith("#!/bin/bash")
    assert "journeyman-remote-runner register" in body
    assert "JOURNEYMAN_REGISTRATION_TOKEN=" in body
    assert "https_proxy=http://proxy.example:3128" in body
    assert "ReadWritePaths=" in body
    assert "/etc/journeyman/runner-pki" in body
    assert "umask 027" in body
    assert "chown -R root:journeyman /opt/journeyman/venv314" in body
    assert "chmod -R g+rX,o-rwx /opt/journeyman/venv314" in body
    assert "runuser -u journeyman -- /opt/journeyman/venv314/bin/python" in body
    assert "systemctl is-active --quiet journeyman-remote-runner" in body
    assert "Journeyman remote runner failed to become active." in body

    with app.app_context():
        runner = db.session.execute(
            db.select(Runner).filter_by(name="remote-corporate-1")
        ).scalar_one()
        assert runner.site == "corporate"
        assert runner.capabilities() == {"ansible", "shell"}
        assert runner.max_concurrent_steps == 3
        assert runner.registration_token_digest
        assert not runner.is_registered


def test_non_resource_admin_cannot_generate_manual_runner_bootstrap(client):
    response = client.get(
        "/runners/manual-bootstrap",
        headers={"X-Test-Username": "ordinary.user"},
    )
    assert response.status_code == 403


def test_manual_runner_bootstrap_form_is_vertical(client):
    response = client.get(
        "/runners/manual-bootstrap",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'class="runner-bootstrap-form"' in body
    assert "flex-direction: column" in body
    assert 'class="form-grid"' not in body


def test_resource_admin_can_generate_manual_runner_update(app, client):
    with app.app_context():
        runner = Runner(
            name="remote-corporate-2",
            site="corporate",
            enabled=True,
            runner_uuid="11111111-2222-3333-4444-555555555555",
            pki_certificate_fingerprint_sha256="aa" * 32,
            pki_certificate_serial="1234",
        )
        runner.set_capabilities(["ansible", "shell"])
        db.session.add(runner)
        db.session.commit()

    response = client.post(
        "/runners/manual-bootstrap",
        data={
            "mode": "update",
            "name": "remote-corporate-2",
            "https_proxy": "http://proxy.example:3128",
            "no_proxy": "journeyman.example",
        },
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert response.mimetype == "text/x-shellscript"
    assert 'filename="journeyman-update-remote-corporate-2.sh"' in response.headers["Content-Disposition"]
    body = response.data.decode("utf-8")
    assert body.startswith("#!/bin/bash")
    assert "EXPECTED_UUID=11111111-2222-3333-4444-555555555555" in body
    assert "journeyman-remote-runner register" not in body
    assert "JOURNEYMAN_REGISTRATION_TOKEN=" not in body
    assert "Existing runner private key is missing or unreadable." in body
    assert "Runner UUID changed during update" in body
    assert "restoring previous runner binaries" in body
    assert "journeyman-remote-runner --version" in body


def test_manual_update_rejects_unenrolled_runner(app, client):
    with app.app_context():
        runner = Runner(name="remote-unenrolled", site="corporate", enabled=True)
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

    response = client.post(
        "/runners/manual-bootstrap",
        data={"mode": "update", "name": "remote-unenrolled"},
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 409
    assert b"Manual update requires an existing enrolled remote runner" in response.data


def test_manual_runner_package_form_exposes_update_mode(client):
    response = client.get(
        "/runners/manual-bootstrap",
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'name="mode" value="bootstrap"' in body
    assert 'name="mode" value="update"' in body
    assert "Update existing enrolled runner" in body
    assert "Generate update script" in body
