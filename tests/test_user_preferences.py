from app import db
from app.models import Project, UserPreference
def test_preferences_default_to_show_disabled(client, app):
    with app.app_context():
        db.session.add(Project(name="Disabled preference project", enabled=False)); db.session.commit()
    response=client.get("/projects",headers={"X-Test-Username":"admin"})
    assert response.status_code==200 and b"Disabled preference project" in response.data
    assert b"Disabled projects hidden" not in response.data
def test_user_can_hide_disabled_projects_and_packages(client, app):
    h={"X-Test-Username":"admin"}
    assert client.post("/preferences",data={"hide_disabled_projects":"1","hide_disabled_packages":"1"},headers=h).status_code==302
    with app.app_context():
        p=UserPreference.query.filter_by(username="admin").one()
        assert p.hide_disabled_projects and p.hide_disabled_packages
    assert b"Disabled projects hidden" in client.get("/projects",headers=h).data
    assert b"Disabled packages hidden" in client.get("/packages",headers=h).data

def test_user_can_choose_rows_per_page(client, app):
    headers = {"X-Test-Username": "admin"}
    response = client.post(
        "/preferences",
        data={"rows_per_page": "100"},
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="admin").one()
        assert preference.rows_per_page == 100


def test_invalid_rows_per_page_falls_back_to_default(client, app):
    headers = {"X-Test-Username": "admin"}
    response = client.post(
        "/preferences",
        data={"rows_per_page": "9999"},
        headers=headers,
    )
    assert response.status_code == 302
    with app.app_context():
        preference = UserPreference.query.filter_by(username="admin").one()
        assert preference.rows_per_page == 50
