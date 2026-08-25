"""ASVS V1 Encoding and Sanitization evidence."""

from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "app" / "templates"
DIRECTORY_SOURCE = ROOT / "app" / "services" / "directory.py"


def test_dynamic_javascript_template_values_use_tojson():
    """Jinja values embedded directly in JS assignments must use tojson."""
    offenders = []
    assignment = re.compile(
        r"(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*\{\{(.*?)\}\}",
        re.DOTALL,
    )

    for path in TEMPLATE_ROOT.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in assignment.finditer(text):
            expression = match.group(1)
            if "tojson" not in expression:
                offenders.append(
                    "{}: {}".format(path.relative_to(ROOT), expression.strip())
                )

    assert offenders == []


def test_directory_dynamic_ldap_filters_escape_untrusted_values():
    """LDAP values are escaped before being inserted into application filters."""
    source = DIRECTORY_SOURCE.read_text(encoding="utf-8")

    assert "from ldap3.utils.conv import escape_filter_chars" in source
    assert "escaped = escape_filter_chars(str(group_name))" in source
    assert "escaped = escape_filter_chars(query)" in source
    assert "escaped_full = escape_filter_chars(raw_username)" in source
    assert "escaped_sam = escape_filter_chars(raw_username.split(\"@\", 1)[0])" in source
    assert "escaped_dn = escape_filter_chars(" in source


def test_package_runtime_values_are_not_interpolated_into_regex_syntax():
    source = (
        ROOT / "app" / "services" / "project_package_launch.py"
    ).read_text(encoding="utf-8")

    # Configured pattern is passed as the expression and the submitted Package
    # value is passed separately as the string to match.
    assert "safe_fullmatch(" in source
    assert "pattern,\n                    value," in source


def test_application_uses_framework_url_generation_and_safe_redirect_helper():
    auth_source = (ROOT / "app" / "auth.py").read_text(encoding="utf-8")
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TEMPLATE_ROOT.rglob("*.html")
    )

    assert "def _safe_next_url" in auth_source
    assert "return redirect(_safe_next_url(" in auth_source
    assert "url_for(" in templates
