import hashlib
import json
from urllib.parse import urlparse
import runpy
import subprocess
from pathlib import Path
from app import db
from app.models import (
    Job,
    JobRepositorySnapshot,
    JobStep,
    JobStepExecutionSlice,
    JobStepHostResult,
    Project,
    Repository,
    Runner,
)
from app.services.runners import (
    RunnerRemovalError,
    ensure_remote_management_target,
    find_runner_for_management,
    issue_registration_token,
    delete_runner,
    runner_removal_references,
)


def test_runners_page_is_admin_only(client):
    response = client.get(
        "/runners",
        headers={"X-Test-Username": "ordinary.user"},
    )

    assert response.status_code == 403


def test_admin_can_create_runner(app, client):
    response = client.post(
        "/runners/new",
        data={
            "name": "Site B Runner",
            "site": "site-b",
            "capabilities": "ansible",
            "max_concurrent_steps": "4",
        },
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"One-time registration token" in response.data

    with app.app_context():
        runner = db.session.execute(
            db.select(Runner).filter_by(name="Site B Runner")
        ).scalar_one()

        assert runner.site == "site-b"
        assert runner.max_concurrent_steps == 4
        assert runner.capabilities() == {"ansible"}
        assert runner.enabled is True
        assert runner.registration_token_digest


def test_admin_can_create_runner_with_multiple_capabilities(app, client):
    response = client.post(
        "/runners/new",
        data={
            "name": "Multi-capability Runner",
            "site": "site-a",
            "capabilities": ["ansible", "shell"],
            "max_concurrent_steps": "2",
        },
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        runner = db.session.execute(
            db.select(Runner).filter_by(name="Multi-capability Runner")
        ).scalar_one()

        assert runner.capabilities() == {"ansible", "shell"}


def test_runner_creation_requires_capability(client):
    response = client.post(
        "/runners/new",
        data={
            "name": "No Capability Runner",
            "site": "site-a",
            "max_concurrent_steps": "1",
        },
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Select at least one runner capability." in response.data

def test_runner_claim_api_bypasses_browser_login_gate(app, client):
    app.config["AUTHENTICATION_DISABLED"] = False

    response = client.post(
        "/api/runners/jobs/claim",
        json={},
    )

    # No runner credentials were supplied, so the runner API itself should
    # reject the request.  Critically, the browser authentication hook must
    # not redirect the runner to /login.
    assert response.status_code == 403
    assert response.is_json
    assert response.get_json() == {
        "error": "Runner authentication failed."
    }
    assert response.headers.get("Location") is None


def test_browser_route_still_redirects_anonymous_user_to_login(app, client):
    app.config["AUTHENTICATION_DISABLED"] = False

    response = client.get(
        "/projects",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert urlparse(location).path == "/login"



def test_runner_registration_and_heartbeat(app, client):
    with app.app_context():
        runner = Runner(
            name="Remote Runner",
            site="remote",
            max_concurrent_steps=2,
        )
        token = issue_registration_token(runner)

        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    registration = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "remote-runner-01",
            "version": "test",
        },
    )

    assert registration.status_code == 200

    registration_data = registration.get_json()
    assert registration_data["runner_uuid"]
    assert registration_data["runner_secret"]

    heartbeat = client.post(
        "/api/runners/heartbeat",
        json={
            "hostname": "remote-runner-01",
            "version": "test",
            "running_steps": 1,
            "load_average_1m": 1.25,
            "load_average_5m": 0.75,
            "cpu_count": 8,
            "free_workspace_bytes": 123456789,
            "status_message": "Ready",
        },
        headers={
            "X-Journeyman-Runner-ID": registration_data["runner_uuid"],
            "Authorization": (
                f"Bearer {registration_data['runner_secret']}"
            ),
        },
    )

    assert heartbeat.status_code == 200
    heartbeat_data = heartbeat.get_json()
    assert heartbeat_data["max_concurrent_steps"] == 2

    with app.app_context():
        runner = db.session.get(Runner, runner_id)

        assert runner.runner_uuid == registration_data["runner_uuid"]
        assert runner.hostname == "remote-runner-01"
        assert runner.version == "test"
        assert runner.running_steps == 1
        assert runner.load_average_1m == 1.25
        assert runner.load_average_5m == 0.75
        assert runner.cpu_count == 8
        assert runner.free_workspace_bytes == 123456789
        assert runner.status_message == "Ready"
        assert runner.last_heartbeat_at is not None
        assert runner.registration_token_digest == ""


def test_runner_registration_token_is_single_use(app, client):
    with app.app_context():
        runner = Runner(name="Single Use")
        token = issue_registration_token(runner)

        db.session.add(runner)
        db.session.commit()

    first = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "single-use-runner",
            "version": "test",
        },
    )

    second = client.post(
        "/api/runners/register",
        json={
            "token": token,
            "hostname": "single-use-runner",
            "version": "test",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 403


def test_remote_runner_claim_respects_site_capability_and_capacity(app, client):
    with app.app_context():
        project = Project(name="Remote claim test", owner="admin")
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="admin",
            execution_type="ansible",
        )
        runner = Runner(name="Site B", site="site-b", max_concurrent_steps=1)
        runner.set_capabilities(["ansible", "shell"])
        token = issue_registration_token(runner)
        db.session.add_all([runner, project, job])
        db.session.commit()

        job.dispatch_target = "remote"
        job.required_runner_site = "site-b"
        job.required_runner_capabilities_json = '["ansible"]'
        db.session.commit()
        job_id = job.id

    registration = client.post("/api/runners/register", json={"token": token})
    credentials = registration.get_json()
    headers = {
        "X-Journeyman-Runner-ID": credentials["runner_uuid"],
        "Authorization": "Bearer {}".format(credentials["runner_secret"]),
    }
    claimed = client.post("/api/runners/jobs/claim", headers=headers)
    assert claimed.status_code == 200
    assert claimed.get_json()["job_id"] == job_id

    duplicate = client.post("/api/runners/jobs/claim", headers=headers)
    assert duplicate.status_code == 204


def test_remote_runner_does_not_claim_mismatched_job(app, client):
    with app.app_context():
        project = Project(name="Remote mismatch test", owner="admin")
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="admin",
            execution_type="ansible",
        )
        runner = Runner(name="Site A", site="site-a")
        runner.set_capabilities(["ansible"])
        token = issue_registration_token(runner)
        db.session.add_all([runner, project, job])
        db.session.commit()
        job.dispatch_target = "remote"
        job.required_runner_site = "site-b"
        db.session.commit()

    registration = client.post("/api/runners/register", json={"token": token})
    credentials = registration.get_json()
    response = client.post(
        "/api/runners/jobs/claim",
        headers={
            "X-Journeyman-Runner-ID": credentials["runner_uuid"],
            "Authorization": "Bearer {}".format(credentials["runner_secret"]),
        },
    )
    assert response.status_code == 204


def _register_remote_job_for_lifecycle(app, client):
    from app.models import JobStep, JobRepositorySnapshot, Repository

    with app.app_context():
        project = Project(name="Remote lifecycle test", owner="admin")
        repository = Repository(name="Lifecycle repository", url="https://example.invalid/repo.git")
        runner = Runner(name="Lifecycle runner", site="site-a")
        runner.set_capabilities(["ansible"])
        registration_token = issue_registration_token(runner)
        db.session.add_all([project, repository, runner])
        db.session.flush()

        repository_path = app.config["REPOSITORY_ROOT"] / str(repository.id)
        repository_path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repository_path, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repository_path, check=True)
        subprocess.run(["git", "config", "user.name", "Journeyman Tests"], cwd=repository_path, check=True)
        (repository_path / "test.yml").write_text("---\n- hosts: all\n  tasks: []\n")
        subprocess.run(["git", "add", "test.yml"], cwd=repository_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "test repository"], cwd=repository_path, check=True)
        repository_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_path,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="admin",
            execution_type="ansible",
            dispatch_target="remote",
        )
        db.session.add(job)
        db.session.flush()
        snapshot = JobRepositorySnapshot(
            job=job,
            repository_id=repository.id,
            repository_name=repository.name,
            repository_url=repository.url,
            repository_commit=repository_commit,
        )
        db.session.add(snapshot)
        db.session.flush()
        step = JobStep(
            job=job,
            job_repository_snapshot_id=snapshot.id,
            position=1,
            name="Test step",
            playbook="test.yml",
        )
        db.session.add(step)
        db.session.commit()
        job_id = job.id

    registration = client.post(
        "/api/runners/register", json={"token": registration_token}
    ).get_json()
    auth_headers = {
        "X-Journeyman-Runner-ID": registration["runner_uuid"],
        "Authorization": "Bearer {}".format(registration["runner_secret"]),
    }
    claim = client.post("/api/runners/jobs/claim", headers=auth_headers)
    assert claim.status_code == 200
    claim_data = claim.get_json()
    dispatch_token = claim_data["dispatch_token"]
    headers = dict(auth_headers)
    headers["X-Journeyman-Dispatch-Token"] = dispatch_token
    return job_id, headers, claim_data


def test_remote_runner_start_control_and_complete_lifecycle(app, client):
    job_id, headers, _claim_data = _register_remote_job_for_lifecycle(app, client)

    started = client.post(
        "/api/runners/jobs/{}/start".format(job_id), headers=headers
    )
    assert started.status_code == 200
    assert started.get_json()["status"] == "running"

    control = client.get(
        "/api/runners/jobs/{}/control".format(job_id), headers=headers
    )
    assert control.status_code == 200
    assert control.get_json()["cancel_requested"] is False

    completed = client.post(
        "/api/runners/jobs/{}/complete".format(job_id),
        headers=headers,
        json={
            "status": "successful",
            "exit_code": 0,
            "message": "Completed remotely.",
            "steps": [
                {
                    "position": 1,
                    "status": "successful",
                    "exit_code": 0,
                    "command": "ansible-playbook test.yml",
                    "stdout": "ok",
                    "stderr": "",
                    "host_results": [
                        {
                            "host": "host01",
                            "status": "successful",
                            "exit_code": 0,
                            "stdout": "hello from host01\n",
                            "stderr": "",
                        },
                        {
                            "host": "host02",
                            "status": "unreachable",
                            "exit_code": None,
                            "stdout": "",
                            "stderr": "Connection timed out",
                        },
                    ],
                }
            ],
        },
    )
    assert completed.status_code == 200

    with app.app_context():
        job = db.session.get(Job, job_id)
        assert job.status == "successful"
        assert job.exit_code == 0
        assert job.dispatch_token == ""
        assert job.steps[0].status == "successful"
        assert job.steps[0].stdout == "ok"
        assert len(job.steps[0].host_results) == 2
        assert job.steps[0].host_results[0].host == "host01"
        assert job.steps[0].host_results[0].exit_code == 0
        assert job.steps[0].host_results[0].stdout == "hello from host01\n"
        assert job.steps[0].host_results[1].host == "host02"
        assert job.steps[0].host_results[1].status == "unreachable"
        assert job.steps[0].host_results[1].exit_code is None
        assert job.steps[0].host_results[1].stderr == "Connection timed out"


def test_remote_runner_control_reports_cancellation(app, client):
    job_id, headers, _claim_data = _register_remote_job_for_lifecycle(app, client)
    assert client.post(
        "/api/runners/jobs/{}/start".format(job_id), headers=headers
    ).status_code == 200

    with app.app_context():
        job = db.session.get(Job, job_id)
        job.status = "cancelling"
        db.session.commit()

    control = client.get(
        "/api/runners/jobs/{}/control".format(job_id), headers=headers
    )
    assert control.status_code == 200
    assert control.get_json()["cancel_requested"] is True

    rejected = client.post(
        "/api/runners/jobs/{}/complete".format(job_id),
        headers=headers,
        json={"status": "successful", "exit_code": 0},
    )
    assert rejected.status_code == 409
    assert rejected.get_json()["error"] == "cancel_pending"

    cancelled = client.post(
        "/api/runners/jobs/{}/complete".format(job_id),
        headers=headers,
        json={"status": "cancelled", "exit_code": 130},
    )
    assert cancelled.status_code == 200


def test_remote_runner_lifecycle_rejects_wrong_dispatch_token(app, client):
    job_id, headers, _claim_data = _register_remote_job_for_lifecycle(app, client)
    headers["X-Journeyman-Dispatch-Token"] = "wrong-token"

    response = client.post(
        "/api/runners/jobs/{}/start".format(job_id), headers=headers
    )
    assert response.status_code == 403


def test_remote_runner_downloads_checksum_verified_repository_artifact(app, client):
    job_id, headers, claim_data = _register_remote_job_for_lifecycle(app, client)
    assert len(claim_data["repositories"]) == 1
    artifact = claim_data["repositories"][0]
    response = client.get(
        "/api/runners/jobs/{}/repositories/{}/artifact".format(
            job_id, artifact["snapshot_id"]
        ),
        headers=headers,
    )
    assert response.status_code == 200
    assert len(response.data) == artifact["size_bytes"]
    assert hashlib.sha256(response.data).hexdigest() == artifact["sha256"]
    assert response.headers["Content-Disposition"].startswith("attachment;")


def test_remote_runner_artifact_rejects_wrong_runner_or_token(app, client):
    job_id, headers, claim_data = _register_remote_job_for_lifecycle(app, client)
    artifact = claim_data["repositories"][0]
    bad_headers = dict(headers)
    bad_headers["X-Journeyman-Dispatch-Token"] = "wrong-token"
    response = client.get(
        "/api/runners/jobs/{}/repositories/{}/artifact".format(
            job_id, artifact["snapshot_id"]
        ),
        headers=bad_headers,
    )
    assert response.status_code == 403


def test_remote_runner_completion_removes_repository_artifacts(app, client):
    job_id, headers, claim_data = _register_remote_job_for_lifecycle(app, client)
    artifact = claim_data["repositories"][0]
    assert client.post(
        "/api/runners/jobs/{}/start".format(job_id), headers=headers
    ).status_code == 200
    assert client.post(
        "/api/runners/jobs/{}/complete".format(job_id),
        headers=headers,
        json={"status": "successful", "exit_code": 0},
    ).status_code == 200
    response = client.get(
        "/api/runners/jobs/{}/repositories/{}/artifact".format(
            job_id, artifact["snapshot_id"]
        ),
        headers=headers,
    )
    assert response.status_code == 403


def _decode_execution_data(envelope, dispatch_token):
    import base64
    import json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    decode = base64.urlsafe_b64decode
    salt = decode(envelope["salt"])
    nonce = decode(envelope["nonce"])
    aad = decode(envelope["aad"])
    ciphertext = decode(envelope["ciphertext"])
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"journeyman remote execution data v1",
    ).derive(dispatch_token.encode("utf-8"))
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    assert hashlib.sha256(plaintext).hexdigest() == envelope["plaintext_sha256"]
    return json.loads(plaintext.decode("utf-8"))


def test_remote_runner_downloads_encrypted_execution_data(app, client, monkeypatch, tmp_path):
    from app.credential_crypto import encrypt_credential_data
    from app.models import JobCredentialSnapshot, JobInventorySnapshot
    from app.services.job_inventory_snapshot import write_job_inventory_snapshot

    monkeypatch.setenv(
        "JOURNEYMAN_INVENTORY_SNAPSHOT_ROOT",
        str(tmp_path / "inventory-snapshots"),
    )
    job_id, headers, claim_data = _register_remote_job_for_lifecycle(app, client)

    with app.app_context():
        job = db.session.get(Job, job_id)
        credential = JobCredentialSnapshot(
            job=job,
            credential_id=None,
            credential_name="Remote machine credential",
            credential_owner="admin",
            credential_type="machine",
            username="automation",
            encrypted_data=encrypt_credential_data(
                {"password": "secret-value"}
            ),
        )
        inventory = JobInventorySnapshot(
            job=job,
            inventory_id=None,
            inventory_name="Remote inventory",
            inventory_type="static",
            version=1,
        )
        db.session.add_all([credential, inventory])
        db.session.flush()
        write_job_inventory_snapshot(
            inventory,
            {
                "all": {"hosts": ["host01"], "children": []},
                "_meta": {"hostvars": {"host01": {"ansible_host": "192.0.2.10"}}},
            },
        )
        job.steps[0].credential_snapshots.append(credential)
        job.steps[0].inventory_snapshot = inventory
        db.session.commit()

    response = client.get(
        "/api/runners/jobs/{}/execution-data".format(job_id), headers=headers
    )
    assert response.status_code == 200
    cache_control = response.headers["Cache-Control"].lower()
    assert "no-store" in cache_control
    assert "private" in cache_control
    envelope = response.get_json()
    payload = _decode_execution_data(
        envelope, headers["X-Journeyman-Dispatch-Token"]
    )
    assert payload["job_id"] == job_id
    assert payload["credentials"][0]["username"] == "automation"
    assert payload["credentials"][0]["data"]["password"] == "secret-value"
    assert payload["inventories"][0]["data"]["_meta"]["hostvars"]["host01"]["ansible_host"] == "192.0.2.10"
    assert payload["steps"][0]["credential_snapshot_ids"] == [payload["credentials"][0]["snapshot_id"]]
    assert payload["steps"][0]["inventory_snapshot_id"] == payload["inventories"][0]["snapshot_id"]
    assert claim_data["execution_data"]["encrypted"] is True


def test_remote_runner_execution_data_rejects_wrong_token(app, client):
    job_id, headers, _claim_data = _register_remote_job_for_lifecycle(app, client)
    headers["X-Journeyman-Dispatch-Token"] = "wrong-token"
    response = client.get(
        "/api/runners/jobs/{}/execution-data".format(job_id), headers=headers
    )
    assert response.status_code == 403


def test_local_runner_heartbeat_is_shown_without_remote_registration(app, client):
    from app.services.runners import runner_health, update_local_runner_heartbeat

    with app.app_context():
        runner = update_local_runner_heartbeat(
            hostname="journeyman",
            version="test",
            running_jobs=1,
            status_message="Executing a local Job",
        )
        assert runner.is_local is True
        assert runner.is_registered is False
        assert runner_health(runner) == "healthy"

    response = client.get("/runners", headers={"X-Test-Username": "admin"})
    assert response.status_code == 200
    assert b"journeyman local runner" in response.data
    assert b"Local / built in" in response.data
    assert b"Managed by service" in response.data


def test_local_runner_cannot_be_disabled_through_remote_runner_actions(app, client):
    from app.services.runners import update_local_runner_heartbeat

    with app.app_context():
        runner = update_local_runner_heartbeat(hostname="journeyman")
        runner_id = runner.id

    response = client.post(
        "/runners/{}/toggle".format(runner_id),
        headers={"X-Test-Username": "admin"},
    )
    assert response.status_code == 400


def test_lost_runner_requeues_job_that_never_started(app):
    from datetime import datetime, timedelta, timezone

    from app.models import Runner
    from app.services.runner_recovery import recover_lost_runner_jobs

    with app.app_context():
        runner = Runner(
            name="lost-runner",
            runner_uuid="11111111-1111-1111-1111-111111111111",
            hostname="lost-runner.example",
            enabled=True,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        runner.set_capabilities(["ansible"])
        project = Project(name="Requeue remote project", owner="admin")
        db.session.add_all([runner, project])
        db.session.flush()
        job = Job(
            project=project,
            project_name=project.name,
            requested_by="admin",
            status="queued",
            dispatch_target="remote",
            assigned_runner_id=runner.id,
            assigned_at=datetime.now(timezone.utc) - timedelta(minutes=9),
            dispatch_token="old-token",
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

        result = recover_lost_runner_jobs()

        job = db.session.get(Job, job_id)
        assert result["requeued"] == [job_id]
        assert job.status == "queued"
        assert job.assigned_runner_id is None
        assert job.assigned_at is None
        assert job.dispatch_token == ""
        assert "returned to the queue" in job.message


def test_lost_runner_fails_started_job_without_requeue(app):
    from datetime import datetime, timedelta, timezone

    from app.models import JobRepositorySnapshot, JobStep, Repository, Runner
    from app.services.runner_recovery import recover_lost_runner_jobs

    with app.app_context():
        runner = Runner(
            name="lost-running-runner",
            runner_uuid="22222222-2222-2222-2222-222222222222",
            hostname="lost-running-runner.example",
            enabled=True,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        runner.set_capabilities(["ansible"])
        project = Project(name="Fail remote project", owner="admin")
        repository = Repository(
            name="Lost runner test repository",
            url="https://example.invalid/lost-runner.git",
        )
        db.session.add_all([runner, project, repository])
        db.session.flush()

        job = Job(
            project=project,
            project_name=project.name,
            requested_by="admin",
            status="running",
            dispatch_target="remote",
            assigned_runner_id=runner.id,
            assigned_at=datetime.now(timezone.utc) - timedelta(minutes=9),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=8),
            dispatch_token="old-token",
        )
        db.session.add(job)
        db.session.flush()

        repository_snapshot = JobRepositorySnapshot(
            job=job,
            repository_id=repository.id,
            repository_name=repository.name,
            repository_url=repository.url,
            repository_commit="0" * 40,
        )
        step = JobStep(
            job=job,
            repository_snapshot=repository_snapshot,
            position=1,
            name="Remote step",
            playbook="site.yml",
            status="running",
        )
        db.session.add_all([repository_snapshot, step])
        db.session.commit()
        job_id = job.id

        result = recover_lost_runner_jobs()

        job = db.session.get(Job, job_id)
        assert result["failed"] == [job_id]
        assert job.status == "failed"
        assert job.exit_code == 1
        assert job.finished_at is not None
        assert job.dispatch_token == ""
        assert job.assigned_runner_id == runner.id
        assert job.steps[0].status == "failed"
        assert "not automatically retried" in job.message


def test_lost_runner_cancelling_job_becomes_cancelled(app):
    from datetime import datetime, timedelta, timezone

    from app.models import Runner
    from app.services.runner_recovery import recover_lost_runner_jobs

    with app.app_context():
        runner = Runner(
            name="lost-cancelling-runner",
            runner_uuid="33333333-3333-3333-3333-333333333333",
            hostname="lost-cancelling-runner.example",
            enabled=True,
            api_secret_digest="digest",
            last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        project = Project(name="Cancel remote project", owner="admin")
        db.session.add_all([runner, project])
        db.session.flush()
        job = Job(
            project=project,
            project_name=project.name,
            requested_by="admin",
            status="cancelling",
            dispatch_target="remote",
            assigned_runner_id=runner.id,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=8),
            dispatch_token="old-token",
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

        result = recover_lost_runner_jobs()

        job = db.session.get(Job, job_id)
        assert result["cancelled"] == [job_id]
        assert job.status == "cancelled"
        assert job.exit_code is None
        assert job.dispatch_token == ""


def test_remote_runner_claim_honours_required_runner_id(app):
    from datetime import datetime, timezone

    from app.services.runner_dispatch import runner_can_claim

    with app.app_context():
        selected = Runner(
            name="selected-routing-runner",
            runner_uuid="33333333-3333-3333-3333-333333333333",
            api_secret_digest="digest-selected",
            site="site-a",
            enabled=True,
            max_concurrent_steps=2,
            running_steps=0,
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        selected.set_capabilities(["ansible"])
        other = Runner(
            name="other-routing-runner",
            runner_uuid="44444444-4444-4444-4444-444444444444",
            api_secret_digest="digest-other",
            site="site-a",
            enabled=True,
            max_concurrent_steps=2,
            running_steps=0,
            last_heartbeat_at=datetime.now(timezone.utc),
        )
        other.set_capabilities(["ansible"])
        project = Project(name="Specific runner routing", owner="admin")
        job = Job(
            project=project,
            project_name=project.name,
            requested_by="admin",
            status="queued",
            dispatch_target="remote",
            required_runner_capabilities_json='["ansible"]',
        )
        db.session.add_all([selected, other, project, job])
        db.session.flush()
        job.required_runner_id = selected.id
        db.session.commit()

        assert runner_can_claim(selected, job) is True
        assert runner_can_claim(other, job) is False


def test_remote_runner_uses_writable_workspace_for_ansible_runtime(tmp_path):
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-remote-runner"
    )
    remote_runner = runpy.run_path(str(runner_path))
    prepare_environment = remote_runner[
        "prepare_ansible_workspace_environment"
    ]

    workspace = tmp_path / "job-1-slice-1"
    workspace.mkdir()

    environment = prepare_environment(
        {
            "HOME": "/var/lib/journeyman",
            "ANSIBLE_CONFIG": "/etc/ansible/ansible.cfg",
        },
        workspace,
    )

    ansible_home = workspace / "private" / "ansible"
    assert environment["HOME"] == "/var/lib/journeyman"
    assert environment["ANSIBLE_HOME"] == str(ansible_home)
    assert environment["ANSIBLE_LOCAL_TEMP"] == str(ansible_home / "tmp")
    assert environment["ANSIBLE_SSH_CONTROL_PATH_DIR"] == str(
        ansible_home / "cp"
    )
    expected_remote_temp = "/tmp/.ansible-journeyman-job-1-slice-1"
    assert environment["ANSIBLE_REMOTE_TEMP"] == expected_remote_temp
    assert environment["ANSIBLE_REMOTE_TMP"] == expected_remote_temp
    assert (ansible_home / "tmp").is_dir()
    assert (ansible_home / "cp").is_dir()
    assert ((ansible_home / "tmp").stat().st_mode & 0o777) == 0o700
    assert ((ansible_home / "cp").stat().st_mode & 0o777) == 0o700


def test_remote_runner_materializes_machine_credential(tmp_path):
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-remote-runner"
    )
    remote_runner = runpy.run_path(str(runner_path))
    materialize = remote_runner[
        "materialize_machine_credential_extra_vars"
    ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    credentials = {
        7: {
            "snapshot_id": 7,
            "type": "machine",
            "username": "automation",
            "data": {
                "password": "ssh-secret",
                "ssh_private_key": "PRIVATE KEY DATA\n",
                "become_password": "become-secret",
                "become_method": "sudo",
                "become_user": "root",
            },
        }
    }
    mapping = {"credential_snapshot_ids": [7]}
    step = {"position": 3}

    variables_path = materialize(
        step,
        "remote_shell",
        credentials,
        mapping,
        workspace,
    )

    values = json.loads(variables_path.read_text(encoding="utf-8"))
    assert values["ansible_user"] == "automation"
    assert values["ansible_password"] == "ssh-secret"
    assert values["ansible_become_password"] == "become-secret"
    assert "ansible_become_method" not in values
    assert "ansible_become_user" not in values

    key_path = Path(values["ansible_private_key_file"])
    assert key_path.read_text(encoding="utf-8") == "PRIVATE KEY DATA\n"
    assert (key_path.stat().st_mode & 0o777) == 0o600
    assert (variables_path.stat().st_mode & 0o777) == 0o600


def test_remote_runner_namespaces_linux_machine_credential_when_environment_credential_is_also_selected(tmp_path):
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-remote-runner"
    )
    remote_runner = runpy.run_path(str(runner_path))
    materialize = remote_runner[
        "materialize_machine_credential_extra_vars"
    ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    credentials = {
        7: {
            "snapshot_id": 7,
            "type": "machine",
            "username": "svc-linux",
            "data": {
                "password": "linux-password",
                "ssh_private_key": "PRIVATE KEY DATA\n",
                "become_method": "sudo",
                "become_user": "root",
            },
        },
        8: {
            "snapshot_id": 8,
            "type": "environment_variables",
            "username": "svc-ansiborg",
            "data": {
                "password": "network-password",
                "username_environment_variable": "ANSIBLE_NET_USERNAME",
                "secret_environment_variable": "ANSIBLE_NET_PASSWORD",
            },
        },
    }
    mapping = {"credential_snapshot_ids": [7, 8]}
    step = {"position": 3}

    variables_path = materialize(
        step,
        "ansible",
        credentials,
        mapping,
        workspace,
    )

    values = json.loads(variables_path.read_text(encoding="utf-8"))
    assert values["linux_ansible_user"] == "svc-linux"
    assert values["linux_ansible_password"] == "linux-password"
    assert "linux_ansible_become_method" not in values
    assert "linux_ansible_become_user" not in values
    assert "linux_ansible_private_key_file" in values
    assert "ansible_user" not in values
    assert "ansible_password" not in values
    assert "ansible_private_key_file" not in values


def test_remote_runner_machine_credential_injects_become_defaults_into_child_environment():
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-remote-runner"
    )
    remote_runner = runpy.run_path(str(runner_path))
    build_environment = remote_runner["step_environment"]

    credentials = {
        7: {
            "snapshot_id": 7,
            "type": "machine",
            "username": "automation",
            "data": {
                "become_method": "sudo",
                "become_user": "root",
            },
        }
    }
    mapping = {"credential_snapshot_ids": [7]}
    step = {"id": 33, "name": "Become test"}
    execution_data = {"job_id": 22}

    environment = build_environment(
        step,
        "ansible",
        credentials,
        mapping,
        execution_data,
    )

    assert environment["ANSIBLE_BECOME_METHOD"] == "sudo"
    assert environment["ANSIBLE_BECOME_USER"] == "root"


def test_admin_can_delete_unused_remote_runner(app, client):
    with app.app_context():
        runner = Runner(name="Disposable Runner", site="test")
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    response = client.post(
        "/runners/{}/delete".format(runner_id),
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b'Disposable Runner' in response.data
    assert b'deleted' in response.data
    with app.app_context():
        assert db.session.get(Runner, runner_id) is None


def test_runner_delete_preserves_historical_slice_and_host_result_provenance(app):
    with app.app_context():
        runner = Runner(
            name="Historical Runner",
            hostname="historical-runner.example.test",
            site="test",
            enabled=False,
        )
        runner.set_capabilities(["ansible"])
        project = Project(name="Historical runner project", owner="admin")
        repository = Repository(
            name="Historical runner repository",
            url="https://git.example.test/historical.git",
        )
        job = Job(
            project=project,
            project_name=project.name,
            status="successful",
            requested_by="admin",
            execution_type="ansible",
        )
        db.session.add_all([runner, project, repository, job])
        db.session.flush()

        repository_snapshot = JobRepositorySnapshot(
            job=job,
            repository_id=repository.id,
            repository_name=repository.name,
            repository_url=repository.url,
            repository_commit="0" * 40,
        )
        db.session.add(repository_snapshot)
        db.session.flush()

        step = JobStep(
            job=job,
            job_repository_snapshot_id=repository_snapshot.id,
            position=1,
            name="Historical step",
            playbook="site.yml",
            status="successful",
        )
        db.session.add(step)
        db.session.flush()

        execution_slice = JobStepExecutionSlice(
            step=step,
            position=1,
            dispatch_target="remote",
            required_runner_id=runner.id,
            assigned_runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            status="successful",
        )
        execution_slice.set_hosts(["host01.example.test"])
        host_result = JobStepHostResult(
            step=step,
            host="host01.example.test",
            status="successful",
            exit_code=0,
            runner_id=runner.id,
            runner_name=runner.name,
            runner_hostname=runner.hostname,
            runner_local=False,
        )
        db.session.add_all([execution_slice, host_result])
        db.session.commit()

        runner_id = runner.id
        slice_id = execution_slice.id
        host_result_id = host_result.id
        references = runner_removal_references(runner)
        assert references["projects"] == 0
        assert references["jobs"] == 0
        assert references["slices"] == 1
        assert references["host_results"] == 1

        delete_runner(runner)

        assert db.session.get(Runner, runner_id) is None
        execution_slice = db.session.get(JobStepExecutionSlice, slice_id)
        host_result = db.session.get(JobStepHostResult, host_result_id)
        assert execution_slice is not None
        assert execution_slice.required_runner_id is None
        assert execution_slice.assigned_runner_id is None
        assert execution_slice.runner_name == "Historical Runner"
        assert execution_slice.runner_hostname == "historical-runner.example.test"
        assert host_result is not None
        assert host_result.runner_id is None
        assert host_result.runner_name == "Historical Runner"
        assert host_result.runner_hostname == "historical-runner.example.test"


def test_runner_delete_refuses_project_reference(app, client):
    with app.app_context():
        runner = Runner(name="Project Default Runner", site="test")
        runner.set_capabilities(["ansible"])
        project = Project(name="Runner reference project", owner="admin")
        db.session.add_all([runner, project])
        db.session.flush()
        project.default_runner_id = runner.id
        db.session.commit()
        runner_id = runner.id

    response = client.post(
        "/runners/{}/delete".format(runner_id),
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"still referenced by 1 Project" in response.data
    with app.app_context():
        assert db.session.get(Runner, runner_id) is not None


def test_admin_can_unregister_remote_runner(app, client):
    with app.app_context():
        runner = Runner(name="Registered Runner", site="test", enabled=True)
        token = issue_registration_token(runner)
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    registration = client.post("/api/runners/register", json={"token": token})
    assert registration.status_code == 200

    response = client.post(
        "/runners/{}/unregister".format(runner_id),
        headers={"X-Test-Username": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        runner = db.session.get(Runner, runner_id)
        assert runner is not None
        assert runner.enabled is False
        assert runner.runner_uuid is None
        assert runner.api_secret_digest == ""
        assert runner.is_registered is False


def test_remote_runner_can_delete_its_unused_registry_record(app, client):
    with app.app_context():
        runner = Runner(name="Self Removing Runner", site="test")
        runner.set_capabilities(["ansible"])
        token = issue_registration_token(runner)
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    registration = client.post("/api/runners/register", json={"token": token})
    credentials = registration.get_json()
    response = client.post(
        "/api/runners/unregister",
        json={"delete": True},
        headers={
            "X-Journeyman-Runner-ID": credentials["runner_uuid"],
            "Authorization": "Bearer {}".format(credentials["runner_secret"]),
        },
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "deleted"
    with app.app_context():
        assert db.session.get(Runner, runner_id) is None


def test_runner_management_resolves_registered_fqdn(app):
    with app.app_context():
        runner = Runner(
            name="runner01",
            hostname="runner01.example.com",
            site="site-a",
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        resolved = find_runner_for_management(
            "runner01.example.com"
        )
        assert resolved.id == runner.id


def test_runner_management_allows_unique_short_name_for_fqdn(app):
    with app.app_context():
        runner = Runner(
            name="runner01",
            hostname="runner01",
            site="site-a",
        )
        runner.set_capabilities(["ansible"])
        db.session.add(runner)
        db.session.commit()

        resolved = find_runner_for_management(
            "runner01.example.com"
        )
        assert resolved.id == runner.id


def test_runner_management_rejects_unknown_runner(app):
    with app.app_context():
        try:
            find_runner_for_management("does-not-exist.example.com")
        except RunnerRemovalError as exc:
            assert "No registered Journeyman runner matches" in str(exc)
        else:
            raise AssertionError("Unknown runner reference was accepted")


def test_runners_page_uses_builtin_management_package_instead_of_add_form(client):
    response = client.get(
        "/runners",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert b"Manage Remote Runner" in response.data
    assert b"<h2>Add runner</h2>" not in response.data
    assert b"Create runner" not in response.data
    assert b"New token" not in response.data


def test_remote_management_rejects_local_runner_hostname(app):
    with app.app_context():
        local = Runner(
            name="journeyman-local",
            hostname="journeyman.example.com",
            is_local=True,
        )
        db.session.add(local)
        db.session.commit()

        try:
            ensure_remote_management_target(
                "journeyman.example.com"
            )
        except RunnerRemovalError as exc:
            assert "Journeyman server itself" in str(exc)
        else:
            raise AssertionError("Journeyman control-plane host was accepted")


def test_remote_management_rejects_loopback(app):
    with app.app_context():
        try:
            ensure_remote_management_target("localhost")
        except RunnerRemovalError as exc:
            assert "localhost" in str(exc).lower()
        else:
            raise AssertionError("localhost was accepted as a remote runner target")


def test_runners_page_has_single_manage_remote_runner_button(app, client):
    with app.app_context():
        from app.services.builtin_automation import ensure_builtin_admin_automation
        ensure_builtin_admin_automation()

    response = client.get(
        "/runners",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert response.data.count(b">Manage Remote Runner</a>") == 1


def test_runner_manage_link_prefills_and_locks_runner_host(app, client):
    with app.app_context():
        from app.services.builtin_automation import ensure_builtin_admin_automation
        package = ensure_builtin_admin_automation()["package"]
        runner = Runner(
            name="runner01.example.test",
            hostname="runner01.example.test",
            site="site-a",
            enabled=True,
        )
        db.session.add(runner)
        db.session.commit()
        package_id = package.id
        runner_id = runner.id

    response = client.get(
        "/packages/{}/launch?runner_id={}".format(package_id, runner_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert b'value="runner01.example.test"' in response.data
    assert b"readonly" in response.data
    assert b"Runner name" in response.data
    assert b"Journeyman server URL" not in response.data


def test_runner_update_available_only_for_older_registered_remote_runner(app):
    from app.services.runners import (
        CURRENT_REMOTE_RUNNER_VERSION,
        runner_update_available,
    )

    with app.app_context():
        older = Runner(
            name="older-runner",
            hostname="older-runner.example.test",
            runner_uuid="older-runner-uuid",
            api_secret_digest="digest",
            enabled=True,
            version="0.4",
        )
        current = Runner(
            name="current-runner",
            hostname="current-runner.example.test",
            runner_uuid="current-runner-uuid",
            api_secret_digest="digest",
            enabled=True,
            version=CURRENT_REMOTE_RUNNER_VERSION,
        )
        newer = Runner(
            name="newer-runner",
            hostname="newer-runner.example.test",
            runner_uuid="newer-runner-uuid",
            api_secret_digest="digest",
            enabled=True,
            version="99.0",
        )
        local = Runner(
            name="local-runner",
            hostname="local-runner.example.test",
            is_local=True,
            version="0.1",
        )

        assert runner_update_available(older) is True
        assert runner_update_available(current) is False
        assert runner_update_available(newer) is False
        assert runner_update_available(local) is False


def test_runners_page_offers_update_for_older_runner_in_action_menu(app, client):
    from app.services.builtin_automation import ensure_builtin_admin_automation
    from app.services.runners import CURRENT_REMOTE_RUNNER_VERSION
    from tests.checks import assert_output_contains, assert_output_excludes

    with app.app_context():
        ensure_builtin_admin_automation()
        runner = Runner(
            name="old-runner.example.test",
            hostname="old-runner.example.test",
            runner_uuid="old-runner-uuid",
            api_secret_digest="digest",
            enabled=True,
            version="0.4",
        )
        db.session.add(runner)
        db.session.commit()
        runner_id = runner.id

    response = client.get("/runners", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    assert_output_contains(
        response.data,
        "Actions",
        purpose="Runner row actions are grouped under the common per-row dropdown",
    )
    assert_output_contains(
        response.data,
        "Update runner to {}".format(CURRENT_REMOTE_RUNNER_VERSION),
        purpose="An older registered remote runner exposes an explicit update action",
    )
    assert_output_contains(
        response.data,
        "runner_id={}&amp;action=update".format(runner_id),
        purpose="The update action targets the selected runner and preselects update mode",
    )
    assert_output_excludes(
        response.data,
        'class="button secondary" type="submit">Disable',
        purpose="Runner Enable/Disable is no longer rendered as a disparate standalone row button",
    )


def test_runner_update_link_prefills_update_action(app, client):
    from app.services.builtin_automation import ensure_builtin_admin_automation
    from tests.checks import assert_output_contains

    with app.app_context():
        package = ensure_builtin_admin_automation()["package"]
        runner = Runner(
            name="dev-runner-1",
            hostname="runner-update.example.test",
            runner_uuid="runner-update-uuid",
            api_secret_digest="digest",
            enabled=True,
            version="0.4",
        )
        db.session.add(runner)
        db.session.commit()
        package_id = package.id
        runner_id = runner.id

    response = client.get(
        "/packages/{}/launch?runner_id={}&action=update".format(package_id, runner_id),
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert_output_contains(
        response.data,
        '&#34;update&#34;',
        purpose="Runner Update renders the Update choice value in the management Package",
    )
    assert_output_contains(
        response.data,
        'value="runner-update.example.test"',
        purpose="Runner Update locks the physical SSH target into the management Package",
    )
    assert_output_contains(
        response.data,
        'value="dev-runner-1"',
        purpose="Runner Update separately locks the logical runner name",
    )


def test_registration_recovery_token_rotates_credentials_only_when_consumed(app):
    from app import db
    from app.models import Runner
    from app.services.runners import (
        authenticate_runner,
        issue_registration_token,
        register_runner,
    )

    with app.app_context():
        runner = Runner(
            name="recovery-runner",
            hostname="recovery-runner.example",
            enabled=True,
        )
        db.session.add(runner)
        initial_token = issue_registration_token(runner)
        db.session.commit()

        registered, initial_secret = register_runner(
            initial_token,
            hostname=runner.hostname,
            version="old",
        )
        runner_id = registered.id
        initial_uuid = registered.runner_uuid
        assert authenticate_runner(initial_uuid, initial_secret).id == runner_id

        recovery_token = issue_registration_token(registered)
        db.session.commit()

        # Merely issuing a repair token must not take a still-running runner
        # offline. Rotation happens only after the remote host consumes it.
        assert authenticate_runner(initial_uuid, initial_secret).id == runner_id

        recovered, recovery_secret = register_runner(
            recovery_token,
            hostname=runner.hostname,
            version="new",
        )
        recovery_uuid = recovered.runner_uuid

        assert recovered.id == runner_id
        assert recovery_uuid != initial_uuid
        assert recovery_secret != initial_secret
        assert recovered.registration_token_digest == ""
        assert authenticate_runner(initial_uuid, initial_secret) is None
        assert authenticate_runner(recovery_uuid, recovery_secret).id == runner_id


def test_runner_heartbeat_records_managed_capabilities(app, client):
    from app import db
    from app.models import Runner
    from app.services.runners import issue_registration_token, register_runner

    with app.app_context():
        runner = Runner(name="capability-runner", hostname="capability-runner.example")
        db.session.add(runner)
        token = issue_registration_token(runner)
        db.session.commit()
        registered, secret = register_runner(token, hostname=runner.hostname, version="test")
        runner_uuid = registered.runner_uuid

    response = client.post(
        "/api/runners/heartbeat",
        headers={
            "X-Journeyman-Runner-ID": runner_uuid,
            "Authorization": "Bearer {}".format(secret),
        },
        json={
            "hostname": "capability-runner.example",
            "version": "test",
            "status_message": "Ready",
            "capabilities": ["ansible", "shell"],
            "managed_capabilities": {
                "syslog_signal_receiver": {
                    "installed": True,
                    "healthy": True,
                    "message": "Ready",
                },
                "snmp_trap_receiver": {
                    "installed": False,
                    "healthy": False,
                    "message": "Requires net-snmp (snmptrapd)",
                },
            },
        },
    )
    assert response.status_code == 200

    with app.app_context():
        runner = Runner.query.filter_by(name="capability-runner").one()
        assert runner.capabilities() == {"ansible", "shell"}
        assert '"syslog_signal_receiver"' in runner.managed_capabilities_json
        assert '"snmp_trap_receiver"' in runner.managed_capabilities_json


def test_syslog_source_requires_syslog_runner_capability(app):
    from app import db
    from app.models import Runner, SignalSource
    from app.services.runner_capabilities import (
        required_runner_capabilities,
        required_runner_packages,
        runner_capability_rows,
        set_reported_capabilities,
    )

    with app.app_context():
        runner = Runner(name="signal-runner", hostname="signal-runner.example", enabled=True)
        db.session.add(runner)
        db.session.flush()
        source = SignalSource(
            name="syslog-test-source",
            source_type="syslog",
            enabled=True,
            runner_id=runner.id,
        )
        db.session.add(source)
        db.session.commit()

        assert required_runner_capabilities(runner) == {"syslog_signal_receiver"}
        assert required_runner_packages(runner) == ["rsyslog"]
        rows = runner_capability_rows(runner)
        assert rows[0]["state"] == "update_required"

        set_reported_capabilities(runner, {
            "syslog_signal_receiver": {
                "installed": True,
                "healthy": True,
                "message": "Ready",
            }
        })
        db.session.commit()
        rows = runner_capability_rows(runner)
        assert rows[0]["state"] == "healthy"


def test_snmp_source_requires_net_snmp_and_current_receiver_configuration(app):
    from app import db
    from app.models import Runner, SignalSource
    from app.services.runner_capabilities import (
        configuration_fingerprint,
        required_runner_capabilities,
        required_runner_packages,
        runner_capability_rows,
        set_reported_capabilities,
        snmp_source_configuration,
    )

    with app.app_context():
        runner = Runner(name="snmp-runner", hostname="snmp-runner.example", enabled=True)
        db.session.add(runner)
        db.session.flush()
        source = SignalSource(
            name="site-snmp-traps",
            source_type="snmp_trap",
            enabled=True,
            runner_id=runner.id,
            snmp_port=162,
        )
        source.set_allowed_networks(["192.0.2.0/24"])
        db.session.add(source)
        db.session.commit()

        assert required_runner_capabilities(runner) == {"snmp_trap_receiver"}
        assert required_runner_packages(runner) == ["net-snmp"]
        desired = snmp_source_configuration(runner)
        assert desired == [{"source_uuid": source.source_uuid, "port": 162}]

        # Installed software alone is not enough: adding/changing an SNMP
        # Source must make the Runner request an Update until its local
        # receiver configuration fingerprint matches Journeyman.
        set_reported_capabilities(runner, {
            "snmp_trap_receiver": {
                "installed": True,
                "healthy": True,
                "message": "Ready",
                "configuration_fingerprint": configuration_fingerprint([]),
            }
        })
        assert runner_capability_rows(runner)[0]["state"] == "update_required"

        set_reported_capabilities(runner, {
            "snmp_trap_receiver": {
                "installed": True,
                "healthy": True,
                "message": "Ready",
                "configuration_fingerprint": configuration_fingerprint(desired),
            }
        })
        assert runner_capability_rows(runner)[0]["state"] == "healthy"


def test_remote_runner_materializes_windows_credential(tmp_path):
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-remote-runner"
    )
    remote_runner = runpy.run_path(str(runner_path))
    materialize = remote_runner[
        "materialize_windows_credential_extra_vars"
    ]

    workspace = tmp_path / "workspace-windows"
    workspace.mkdir()
    credentials = {
        8: {
            "snapshot_id": 8,
            "type": "windows",
            "username": "DOMAIN\\svc-journeyman",
            "data": {
                "password": "windows-secret",
                "extra_vars": {
                    "win_ansible_user": "{{ user }}",
                    "win_ansible_password": "{{ passwd }}",
                    "win_ansible_connection": "winrm",
                    "win_ansible_winrm_transport": "kerberos",
                },
            },
        }
    }
    mapping = {"credential_snapshot_ids": [8]}
    step = {"position": 4}

    variables_path = materialize(
        step,
        "ansible",
        credentials,
        mapping,
        workspace,
    )

    values = json.loads(variables_path.read_text(encoding="utf-8"))
    assert values == {
        "win_ansible_user": "DOMAIN\\svc-journeyman",
        "win_ansible_password": "windows-secret",
        "win_ansible_connection": "winrm",
        "win_ansible_winrm_transport": "kerberos",
    }
    assert (variables_path.stat().st_mode & 0o777) == 0o600
