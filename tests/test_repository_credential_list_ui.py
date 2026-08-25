from app import db
from app.models import Credential, Repository


def test_repositories_use_standard_table_and_actions_dropdown(app, client):
    with app.app_context():
        repository = Repository(
            name="Example repository",
            description="Repository UI test",
            repository_type="directory",
            directory_path="/tmp/example-repository",
            default_branch="main",
            status="up_to_date",
        )
        db.session.add(repository)
        db.session.commit()
        repository_id = repository.id

    response = client.get("/repositories", headers={"X-Test-Username": "admin"})
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "action-menu-trigger" in html
    assert "Refresh Directory Snapshot" in html
    assert 'href="/repositories/{}/edit"'.format(repository_id) in html
    assert ">Delete<" in html
    assert "Repository Details" not in html
    assert "Search repositories" not in html


def test_credentials_use_standard_table_and_actions_dropdown(app, client):
    with app.app_context():
        credential = Credential(
            name="Example credential",
            description="Credential UI test",
            owner="credential.owner",
            security_scope="private",
            credential_type="environment_variables",
            username="example-user",
        )
        credential.set_credential_data({"password": "not-rendered-in-list"})
        db.session.add(credential)
        db.session.commit()
        credential_id = credential.id

    response = client.get(
        "/credentials",
        headers={"X-Test-Username": "credential.owner"},
    )
    html = response.data.decode("utf-8")

    assert response.status_code == 200
    assert "action-menu-trigger" in html
    assert "Reveal stored secret" in html
    assert 'href="/credentials/{}/edit"'.format(credential_id) in html
    assert ">Delete<" in html
    assert "Credential Details" not in html
    assert "Search credentials" not in html
    assert "not-rendered-in-list" not in html
