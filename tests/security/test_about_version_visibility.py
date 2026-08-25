from flask import g, render_template
import pytest


pytestmark = pytest.mark.security


def _render_base(app, *, role):
    with app.test_request_context("/"):
        g.authenticated_username = "test-user"
        g.authenticated_role = role
        g.authenticated_display_name = "Test User"
        g.authenticated_group_names = frozenset()
        g.authenticated_user_object_guid = None
        g.authenticated_group_object_guids = frozenset()
        g.authenticated_via = "ldap"
        g.authenticated_session_id = "about-test-session"
        g.break_glass_activated_at = None
        g.break_glass_expires_at = None
        return render_template("base.html")


def test_about_is_available_to_all_authenticated_users(app):
    html = _render_base(app, role="User")

    assert "About Journeyman" in html
    assert "Journeyman version" in html
    assert app.config["JOURNEYMAN_VERSION"] in html


def test_about_dependency_versions_are_not_rendered_for_non_admin(app, monkeypatch):
    def fake_inventory():
        return (
            {
                "name": "SensitivePackageName",
                "version": "9.8.7",
                "direct": True,
            },
        )

    monkeypatch.setattr(
        "app.services.about.runtime_dependency_inventory",
        fake_inventory,
    )

    html = _render_base(app, role="User")

    assert "Python dependencies" not in html
    assert "SensitivePackageName" not in html
    assert "9.8.7" not in html


def test_about_dependency_versions_are_rendered_for_admins(app, monkeypatch):
    def fake_inventory():
        return (
            {
                "name": "ExampleDirect",
                "version": "1.2.3",
                "direct": True,
            },
            {
                "name": "ExampleTransitive",
                "version": "4.5.6",
                "direct": False,
            },
        )

    monkeypatch.setattr(
        "app.services.about.runtime_dependency_inventory",
        fake_inventory,
    )
    monkeypatch.setattr(
        "app.services.about.runtime_python_version",
        lambda: "3.14.0",
    )

    html = _render_base(app, role="Administrator")

    assert "Python runtime" in html
    assert "Python dependencies" in html
    assert "3.14.0" in html
    assert "ExampleDirect" in html
    assert "1.2.3" in html
    assert "ExampleTransitive" in html
    assert "4.5.6" in html
