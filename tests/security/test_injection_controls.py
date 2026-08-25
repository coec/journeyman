"""Cross-cutting injection and output-encoding security regressions."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import db
from app.models import Repository
from app.services.inventory_resolver import (
    InventoryResolutionError,
    _resolve_binding_string,
)


pytestmark = pytest.mark.security

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SECURITY_ROOTS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "bin",
    PROJECT_ROOT / "deployment",
)
TEMPLATE_ROOT = PROJECT_ROOT / "app" / "templates"


def _is_python_source(path):
    if path.suffix == ".py":
        return True

    try:
        first_line = path.open("r", encoding="utf-8").readline()
    except (OSError, UnicodeDecodeError):
        return False

    return first_line.startswith("#!") and "python" in first_line.lower()


def _python_files():
    for root in PYTHON_SECURITY_ROOTS:
        if root.is_file():
            if _is_python_source(root):
                yield root
            continue
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and _is_python_source(path):
                yield path


def test_bobby_tables_repository_name_remains_data(app, client):
    payload = "Robert'); DROP TABLE repositories;--"

    response = client.post(
        "/repositories/new",
        headers={"X-Test-Username": "admin"},
        data={
            "name": payload,
            "description": "Bobby Tables regression",
            "url": "git@example.invalid:repository.git",
            "default_branch": "main",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        repository = Repository.query.filter_by(name=payload).one()
        assert repository.name == payload
        assert Repository.query.count() >= 1


def test_repository_name_is_html_escaped(app, client):
    payload = '<script>alert("journeyman-xss")</script>'

    with app.app_context():
        repository = Repository(
            name=payload,
            description="XSS regression",
            url="https://example.invalid/xss.git",
            default_branch="main",
        )
        db.session.add(repository)
        db.session.commit()

    response = client.get(
        "/repositories",
        headers={"X-Test-Username": "admin"},
    )

    assert response.status_code == 200
    assert payload.encode("utf-8") not in response.data
    assert b"&lt;script&gt;" in response.data
    assert b"journeyman-xss" in response.data


def test_templates_do_not_disable_jinja_autoescaping():
    forbidden_fragments = (
        "|safe",
        "| safe",
        "{% autoescape false %}",
        "{% autoescape false -%}",
    )

    offenders = []
    for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
        content = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in content:
                offenders.append("{}: {}".format(path.relative_to(PROJECT_ROOT), fragment))

    assert offenders == []


def test_application_does_not_enable_shell_interpretation_for_subprocesses():
    offenders = []

    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            # Executable runner scripts without a .py suffix are still Python;
            # fail loudly if they stop being parseable.
            raise

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            function = node.func
            if isinstance(function, ast.Attribute):
                if (
                    isinstance(function.value, ast.Name)
                    and function.value.id == "os"
                    and function.attr in {"system", "popen"}
                ):
                    offenders.append(
                        "{}:{} uses os.{}".format(
                            path.relative_to(PROJECT_ROOT), node.lineno, function.attr
                        )
                    )

                if (
                    isinstance(function.value, ast.Name)
                    and function.value.id == "subprocess"
                    and function.attr
                    in {"run", "Popen", "call", "check_call", "check_output"}
                ):
                    for keyword in node.keywords:
                        if keyword.arg != "shell":
                            continue
                        if not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is False
                        ):
                            offenders.append(
                                "{}:{} enables or dynamically controls shell interpretation".format(
                                    path.relative_to(PROJECT_ROOT), node.lineno
                                )
                            )

    assert offenders == []


def test_application_does_not_use_dynamic_eval_or_exec():
    offenders = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in {"eval", "exec"}:
                offenders.append(
                    "{}:{} uses {}()".format(
                        path.relative_to(PROJECT_ROOT), node.lineno, node.func.id
                    )
                )

    assert offenders == []


def test_yaml_deserialization_uses_safe_loaders_only():
    offenders = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "yaml":
                continue
            if node.func.attr in {"load", "load_all", "full_load", "unsafe_load"}:
                offenders.append(
                    "{}:{} uses yaml.{}".format(
                        path.relative_to(PROJECT_ROOT), node.lineno, node.func.attr
                    )
                )

    assert offenders == []


def test_inventory_bindings_reject_general_jinja_expressions():
    with pytest.raises(InventoryResolutionError, match="unsupported inventory binding expression"):
        _resolve_binding_string(
            "{{ config.SECRET_KEY }}",
            {"clustername": "lab01"},
            "Security inventory",
        )

    with pytest.raises(InventoryResolutionError, match="unsupported inventory binding expression"):
        _resolve_binding_string(
            "{% for item in values %}{{ item }}{% endfor %}",
            {"clustername": "lab01"},
            "Security inventory",
        )

    with pytest.raises(InventoryResolutionError, match="unsupported inventory binding expression"):
        _resolve_binding_string(
            "{# hidden template comment #}{{ clustername }}",
            {"clustername": "lab01"},
            "Security inventory",
        )

    with pytest.raises(InventoryResolutionError, match="unsupported inventory binding expression"):
        _resolve_binding_string(
            "{{ helper() }}",
            {"clustername": "lab01"},
            "Security inventory",
        )


def test_inventory_bindings_allow_declared_scalar_substitution_only():
    assert (
        _resolve_binding_string(
            "hg-{{ clustername }}",
            {"clustername": "lab01"},
            "Security inventory",
        )
        == "hg-lab01"
    )

    with pytest.raises(InventoryResolutionError, match="must be scalar"):
        _resolve_binding_string(
            "{{ clustername }}",
            {"clustername": ["lab01", "lab02"]},
            "Security inventory",
        )
