import pytest
import json

from flask import g

from app import db
from app.models import AuditLog, Job, JobPackageSnapshot, Project
from app.services.audit import record_audit_event

pytestmark = pytest.mark.security



def test_admin_can_view_audit_log(app, client):
    with app.app_context():
        db.session.add(
            AuditLog(
                actor_username="admin",
                actor_role="Administrator",
                action="team.create",
                object_type="team",
                object_id="7",
                object_name="Network Operations",
                result="success",
                source_ip="127.0.0.1",
                details_json="{}",
            )
        )
        db.session.commit()

    response = client.get("/audit-log", headers={"X-Test-Username": "admin"})

    assert response.status_code == 200
    assert b"Audit Log" in response.data
    assert b"team.create" in response.data
    assert b"Network Operations" in response.data


def test_user_cannot_view_audit_log(client):
    response = client.get("/audit-log", headers={"X-Test-Username": "ordinary.user"})
    assert response.status_code == 403


def test_audit_service_redacts_sensitive_detail_keys(app):
    with app.test_request_context("/test", headers={"X-Forwarded-For": "192.0.2.10"}):
        g.authenticated_username = "admin"
        g.authenticated_role = "Administrator"
        g.authenticated_user_object_guid = None
        g.authenticated_via = "ldap"
        event = record_audit_event(
            "credential.update",
            details={
                "credential_name": "Machine credential",
                "password": "must-not-be-stored",
                "nested": {"api_token": "also-secret", "safe": "visible"},
            },
        )

        details = json.loads(event.details_json)
        assert details["password"] == "[redacted]"
        assert details["nested"]["api_token"] == "[redacted]"
        assert details["nested"]["safe"] == "visible"
        assert "must-not-be-stored" not in event.details_json
        assert "also-secret" not in event.details_json


@pytest.mark.parametrize(
    ("job_status", "audit_result"),
    (
        ("successful", "success"),
        ("failed", "failure"),
        ("cancelled", "cancelled"),
    ),
)
def test_terminal_project_job_appends_audit_outcome(app, job_status, audit_result):
    with app.app_context():
        project = Project(name="Terminal audit project")
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="alice",
        )
        db.session.add(job)
        db.session.commit()

        job.status = job_status
        db.session.commit()

        event = AuditLog.query.filter_by(
            action="job.completed",
            object_type="job",
            object_id=str(job.id),
        ).one()
        details = json.loads(event.details_json)

        assert event.actor_username == "system"
        assert event.authenticated_via == "job-lifecycle"
        assert event.object_name == project.name
        assert event.result == audit_result
        assert details["job_id"] == job.id
        assert details["job_status"] == job_status
        assert details["requested_by"] == "alice"


def test_terminal_manage_remote_runner_job_audits_package_and_action(app):
    with app.app_context():
        project = Project(name="ZZ - Manage Remote Runner")
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="acal002",
        )
        snapshot = JobPackageSnapshot(
            package_id=4,
            package_name="ZZ - Manage Remote Runner",
            package_owner="__journeyman_builtin__",
            step_limit="",
        )
        snapshot.set_package_definition({"name": snapshot.package_name})
        snapshot.set_display_values(
            [
                {
                    "variable_name": "journeyman_manage_action",
                    "label": "Action",
                    "value": "install",
                    "display_value": "Install / register",
                    "display_role": "confirmation_critical",
                    "binding_type": "extra_var",
                    "is_secret": False,
                }
            ]
        )
        snapshot.set_operational_targets(["runner.example.test", "runner-1"])
        snapshot.set_inventory_bindings({})
        snapshot.set_execution_vars(
            {
                "journeyman_manage_action": "install",
                "journeyman_registration_token": "must-not-be-audited",
            }
        )
        job.package_snapshot = snapshot
        db.session.add(job)
        db.session.commit()

        job.status = "successful"
        db.session.commit()

        event = AuditLog.query.filter_by(
            action="package.execute.completed",
            object_type="job",
            object_id=str(job.id),
        ).one()
        details = json.loads(event.details_json)

        assert event.result == "success"
        assert event.object_name == "ZZ - Manage Remote Runner"
        assert details["package_id"] == 4
        assert details["management_action"] == "install"
        assert details["operational_targets"] == [
            "runner.example.test",
            "runner-1",
        ]
        assert "must-not-be-audited" not in event.details_json


def test_terminal_job_audit_event_is_not_duplicated(app):
    with app.app_context():
        project = Project(name="Deduplicated terminal audit project")
        job = Job(
            project=project,
            project_name=project.name,
            status="queued",
            requested_by="alice",
        )
        db.session.add(job)
        db.session.commit()

        job.status = "failed"
        db.session.commit()
        job.status = "failed"
        db.session.commit()

        assert AuditLog.query.filter_by(
            action="job.completed",
            object_type="job",
            object_id=str(job.id),
        ).count() == 1
