import pytest
import json

from flask import g

from app import db
from app.models import AuditLog
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
