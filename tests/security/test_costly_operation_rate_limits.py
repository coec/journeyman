"""ASVS 2.4.1 evidence for bounded resource-intensive operations."""

from datetime import datetime, timezone

import pytest

from app import db
from app.models import AuditLog
from app.services.costly_operation_rate_limit import (
    check_and_record_costly_operation,
)


pytestmark = pytest.mark.security


def _attempt(operation, username):
    return AuditLog(
        occurred_at=datetime.now(timezone.utc),
        actor_username=username,
        action="security.costly_operation_attempt",
        object_type="costly_operation",
        object_name=operation,
        result="attempt",
        details_json="{}",
    )


def test_per_user_costly_operation_limit_returns_429(app):
    app.config.update(
        COSTLY_OPERATION_WINDOW_SECONDS=300,
        COSTLY_LAUNCH_USER_LIMIT=2,
        COSTLY_LAUNCH_GLOBAL_LIMIT=10,
    )
    with app.app_context():
        db.session.add_all([
            _attempt("execution_launch", "admin"),
            _attempt("execution_launch", "admin"),
        ])
        db.session.commit()

    with app.test_request_context("/", headers={"X-Test-Username": "admin"}):
        from flask import g
        g.authenticated_username = "admin"
        response = check_and_record_costly_operation("execution_launch")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "300"


def test_global_costly_operation_limit_applies_across_users(app):
    app.config.update(
        COSTLY_OPERATION_WINDOW_SECONDS=300,
        COSTLY_INVENTORY_USER_LIMIT=10,
        COSTLY_INVENTORY_GLOBAL_LIMIT=2,
    )
    with app.app_context():
        db.session.add_all([
            _attempt("inventory_refresh", "user-a"),
            _attempt("inventory_refresh", "user-b"),
        ])
        db.session.commit()

    with app.test_request_context("/"):
        from flask import g
        g.authenticated_username = "admin"
        response = check_and_record_costly_operation("inventory_refresh")
        assert response.status_code == 429


def test_allowed_attempt_is_persisted_before_expensive_work(app):
    app.config.update(
        COSTLY_OPERATION_WINDOW_SECONDS=300,
        COSTLY_REPOSITORY_USER_LIMIT=5,
        COSTLY_REPOSITORY_GLOBAL_LIMIT=20,
    )
    with app.test_request_context("/"):
        from flask import g
        g.authenticated_username = "admin"
        assert check_and_record_costly_operation("repository_sync") is None

    with app.app_context():
        assert AuditLog.query.filter_by(
            action="security.costly_operation_attempt",
            object_type="costly_operation",
            object_name="repository_sync",
            actor_username="admin",
        ).count() == 1


def test_all_expensive_route_families_are_rate_limited():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    expected = {
        "app/views/projects.py": (
            'costly_operation_rate_limit("execution_preview")',
            'costly_operation_rate_limit("execution_launch")',
        ),
        "app/views/packages.py": (
            'costly_operation_rate_limit("execution_preview")',
            'costly_operation_rate_limit("execution_launch")',
        ),
        "app/views/inventories.py": (
            'costly_operation_rate_limit("inventory_refresh")',
        ),
        "app/views/repositories.py": (
            'costly_operation_rate_limit("repository_sync")',
        ),
        "app/views/environments.py": (
            'costly_operation_rate_limit("environment_build")',
            'check_and_record_costly_operation("environment_build")',
        ),
    }
    for relative_path, markers in expected.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source
