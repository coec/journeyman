from pathlib import Path

from werkzeug.security import check_password_hash

from app import db
from app.cli import _write_hash_file
from app.models import FallbackAdminActivation
from app.services.fallback_admin import fallback_admin_activation_is_non_expiring
from journeyman_configuration import load_journeyman_configuration

def test_fallback_admin_hash_write_uses_nonpredictable_temporary_file(tmp_path):
    destination = tmp_path / "fallback-admin-password.hash"
    predictable = tmp_path / "fallback-admin-password.hash.tmp"
    predictable.write_text("do-not-touch\n", encoding="utf-8")

    _write_hash_file(destination, "test-password")

    stored_hash = destination.read_text(encoding="utf-8").strip()
    assert check_password_hash(stored_hash, "test-password")
    assert predictable.read_text(encoding="utf-8") == "do-not-touch\n"
    assert not list(tmp_path.glob(".fallback-admin-password.hash.*"))


def test_fallback_admin_generate_reports_default_lifetime(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["fallback-admin", "generate", "--length", "24"])

    assert result.exit_code == 0, result.output
    assert "Maximum lifetime: 60 minutes" in result.output
    assert "Signing out expires this activation immediately." in result.output
    assert "Break-glass access expires at:" in result.output


def test_fallback_admin_generate_uses_configured_lifetime(app):
    app.config["FALLBACK_ADMIN_LIFETIME_MINUTES"] = 10080
    runner = app.test_cli_runner()
    result = runner.invoke(args=["fallback-admin", "generate", "--length", "24"])
    assert result.exit_code == 0, result.output
    assert "Maximum lifetime: 10080 minutes" in result.output

    with app.app_context():
        activation = db.session.get(FallbackAdminActivation, 1)
        assert int((activation.expires_at - activation.activated_at).total_seconds()) == 10080 * 60


def test_fallback_admin_generate_cli_lifetime_overrides_configuration(app):
    app.config["FALLBACK_ADMIN_LIFETIME_MINUTES"] = 60
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "fallback-admin",
            "generate",
            "--length",
            "24",
            "--lifetime-minutes",
            "120",
        ]
    )
    assert result.exit_code == 0, result.output
    assert "Maximum lifetime: 120 minutes" in result.output


def test_fallback_admin_generate_no_expiry_is_explicit_and_warned(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["fallback-admin", "generate", "--length", "24", "--no-expiry"]
    )
    assert result.exit_code == 0, result.output
    assert "Break-glass access expiry: none" in result.output
    assert "Maximum lifetime: no automatic expiry" in result.output
    assert "strongly discouraged for production deployments" in result.output
    assert "Signing out expires this activation immediately." in result.output

    with app.app_context():
        activation = db.session.get(FallbackAdminActivation, 1)
        assert activation.expires_at.year == 9999
        assert fallback_admin_activation_is_non_expiring(activation) is True


def test_fallback_admin_generate_rejects_conflicting_lifetime_options(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "fallback-admin",
            "generate",
            "--length",
            "24",
            "--lifetime-minutes",
            "120",
            "--no-expiry",
        ]
    )
    assert result.exit_code != 0
    assert "cannot be combined" in result.output


def test_fallback_admin_generate_rejects_negative_configured_lifetime_before_hash_write(app, tmp_path):
    destination = tmp_path / "fallback-admin-password.hash"
    destination.write_text("existing-hash\n", encoding="utf-8")
    app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"] = str(destination)
    app.config["FALLBACK_ADMIN_LIFETIME_MINUTES"] = -1

    runner = app.test_cli_runner()
    result = runner.invoke(args=["fallback-admin", "generate", "--length", "24"])

    assert result.exit_code != 0
    assert "cannot be negative" in result.output
    assert destination.read_text(encoding="utf-8") == "existing-hash\n"


def test_fallback_admin_generate_rejects_unrepresentable_lifetime_before_hash_write(app, tmp_path):
    destination = tmp_path / "fallback-admin-password.hash"
    destination.write_text("existing-hash\n", encoding="utf-8")
    app.config["FALLBACK_ADMIN_PASSWORD_HASH_FILE"] = str(destination)

    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "fallback-admin",
            "generate",
            "--length",
            "24",
            "--lifetime-minutes",
            "999999999999999999",
        ]
    )

    assert result.exit_code != 0
    assert "is too large" in result.output
    assert destination.read_text(encoding="utf-8") == "existing-hash\n"


def test_fallback_admin_lifetime_is_supported_in_yaml_configuration(tmp_path, monkeypatch):
    config_path = tmp_path / "journeyman.yml"
    config_path.write_text(
        "version: 1\nauthentication:\n  fallback_admin_lifetime_minutes: 10080\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JOURNEYMAN_FALLBACK_ADMIN_LIFETIME_MINUTES", raising=False)

    assert load_journeyman_configuration(config_path) is True
    assert os.environ["JOURNEYMAN_FALLBACK_ADMIN_LIFETIME_MINUTES"] == "10080"


def test_break_glass_ui_does_not_assume_sixty_minute_lifetime():
    template = Path("app/templates/base.html").read_text(encoding="utf-8")
    script = Path("app/static/js/journeyman.js").read_text(encoding="utf-8")

    assert "data-break-glass-non-expiring" in template
    assert "automatic expiry disabled" in template
    assert 'body.dataset.breakGlassNonExpiring === "true"' in script
    assert "const warningFractions = [0.5, 0.75, 5 / 6, 11 / 12];" in script
    assert "const warningMinutes = [30, 45, 50, 55];" not in script
    assert "${60 - elapsedMinutes}" not in script

