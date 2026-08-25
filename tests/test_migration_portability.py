import ast
import re
from pathlib import Path


MIGRATION_ROOT = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
)


def _is_boolean_type(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Boolean"
    )


def _column_name(call):
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def test_migrations_do_not_use_integer_defaults_for_booleans():
    failures = []

    for path in sorted(MIGRATION_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        boolean_columns = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "Column"
            ):
                continue
            if len(node.args) < 2 or not _is_boolean_type(node.args[1]):
                continue

            name = _column_name(node)
            if name:
                boolean_columns.add(name)

            for keyword in node.keywords:
                if keyword.arg != "server_default":
                    continue
                value = keyword.value
                if (
                    isinstance(value, ast.Constant)
                    and value.value in (0, 1, "0", "1")
                ):
                    failures.append(
                        "{}:{} boolean column {!r} uses numeric server_default {!r}".format(
                            path.name,
                            getattr(node, "lineno", "?"),
                            name,
                            value.value,
                        )
                    )

        if not boolean_columns:
            continue

        raw_sql = "\n".join(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        )
        for name in sorted(boolean_columns):
            if re.search(
                r"\\bSET\\s+{}\\s*=\\s*[01]\\b".format(re.escape(name)),
                raw_sql,
                flags=re.IGNORECASE,
            ):
                failures.append(
                    "{} raw SQL assigns 0/1 to boolean column {!r}".format(
                        path.name,
                        name,
                    )
                )

    assert not failures, "\n".join(failures)
