from pathlib import Path

from werkzeug.security import check_password_hash

from app.cli import _write_hash_file


def test_fallback_admin_hash_write_uses_nonpredictable_temporary_file(tmp_path):
    destination = tmp_path / "fallback-admin-password.hash"
    predictable = tmp_path / "fallback-admin-password.hash.tmp"
    predictable.write_text("do-not-touch\n", encoding="utf-8")

    _write_hash_file(destination, "test-password")

    stored_hash = destination.read_text(encoding="utf-8").strip()
    assert check_password_hash(stored_hash, "test-password")
    assert predictable.read_text(encoding="utf-8") == "do-not-touch\n"
    assert not list(tmp_path.glob(".fallback-admin-password.hash.*"))


def test_fallback_admin_generate_reports_fixed_lifetime(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["fallback-admin", "generate", "--length", "24"])

    assert result.exit_code == 0, result.output
    assert "Maximum lifetime: 60 minutes" in result.output
    assert "Signing out expires this activation immediately." in result.output
    assert "Break-glass access expires at:" in result.output
