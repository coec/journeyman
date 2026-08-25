import pytest
from datetime import datetime, timezone

import app.views.system_status as routes


pytestmark = pytest.mark.security

def test_system_status_is_admin_only(client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "collect_system_status",
        lambda: {},
    )

    response = client.get(
        "/system-status",
        headers={
            "X-Test-Username": "ordinary.user",
        },
    )

    assert response.status_code == 403


def test_system_status_page_renders_for_admin(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        routes,
        "collect_system_status",
        lambda: {
            "checked_at": datetime.now(timezone.utc),
            "overall": "healthy",
            "storage": [
                {
                    "name": "Journeyman data",
                    "path": "/var/lib/journeyman",
                    "mount_point": "/",
                    "status": "healthy",
                    "total_display": "100.0 GiB",
                    "used_display": "25.0 GiB",
                    "free_display": "75.0 GiB",
                    "used_percent": 25.0,
                    "error": "",
                },
                {
                    "name": "Journeyman application",
                    "path": "/opt/journeyman",
                    "mount_point": "/",
                    "status": "healthy",
                    "total_display": "100.0 GiB",
                    "used_display": "25.0 GiB",
                    "free_display": "75.0 GiB",
                    "used_percent": 25.0,
                    "error": "",
                },
            ],
            "checks": [
                {
                    "name": "Database",
                    "status": "healthy",
                    "summary": "Database is responding.",
                    "details": "Migration revision: test",
                }
            ],
            "job_counts": {
                "queued": 1,
                "running": 2,
                "failed": 3,
                "cancelled": 4,
            },
            "repository_issues": 5,
            "inventory_issues": 6,
            "environment_issues": 7,
            "hostname": "test-host",
            "runners": [],
        },
    )

    response = client.get(
        "/system-status",
        headers={
            "X-Test-Username": "admin",
        },
    )

    assert response.status_code == 200
    assert b"System Status" in response.data
    assert b"Database is responding" in response.data
    assert b"test-host" in response.data
    assert b"/var/lib/journeyman" in response.data
    assert b"/opt/journeyman" in response.data
    assert b"Both paths use the same backing filesystem" in response.data


def test_storage_path_status_thresholds(tmp_path, monkeypatch):
    from collections import namedtuple

    from app.services import system_status

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        system_status.shutil,
        "disk_usage",
        lambda path: Usage(1000, 850, 150),
    )
    monkeypatch.setattr(
        system_status,
        "_mount_point",
        lambda path: "/test-mount",
    )

    storage = system_status.storage_path_status(
        tmp_path,
        "Test storage",
    )

    assert storage["status"] == "warning"
    assert storage["used_percent"] == 85.0
    assert storage["mount_point"] == "/test-mount"


def test_storage_path_status_is_failed_at_ninety_percent(tmp_path, monkeypatch):
    from collections import namedtuple

    from app.services import system_status

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(
        system_status.shutil,
        "disk_usage",
        lambda path: Usage(1000, 900, 100),
    )

    storage = system_status.storage_path_status(
        tmp_path,
        "Test storage",
    )

    assert storage["status"] == "failed"
