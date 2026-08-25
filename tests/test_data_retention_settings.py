from app import db
from app.models import SystemSetting


def _headers(username="admin"):
    return {"X-Test-Username": username}


def test_data_retention_settings_default_to_180_days(app, client):
    response = client.get("/settings/data-retention", headers=_headers())
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'name="job_retention_days"' in body
    assert 'name="reaction_retention_days"' in body
    assert 'value="180"' in body

    with app.app_context():
        settings = db.session.get(SystemSetting, 1)
        assert settings.job_retention_days == 180
        assert settings.reaction_retention_days == 180


def test_admin_can_update_data_retention_settings(app, client):
    response = client.post(
        "/settings/data-retention",
        data={
            "job_retention_days": "90",
            "reaction_retention_days": "30",
        },
        headers=_headers(),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(SystemSetting, 1)
        assert settings.job_retention_days == 90
        assert settings.reaction_retention_days == 30


def test_data_retention_zero_means_indefinite(app, client):
    response = client.post(
        "/settings/data-retention",
        data={
            "job_retention_days": "0",
            "reaction_retention_days": "0",
        },
        headers=_headers(),
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        settings = db.session.get(SystemSetting, 1)
        assert settings.job_retention_days == 0
        assert settings.reaction_retention_days == 0


def test_non_admin_cannot_change_data_retention(client):
    response = client.post(
        "/settings/data-retention",
        data={
            "job_retention_days": "30",
            "reaction_retention_days": "30",
        },
        headers=_headers("alice"),
    )
    assert response.status_code == 403
