"""Guards against accidentally adding exact duplicate pytest tests."""

import ast
from collections import defaultdict
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent


def _normalised_test_ast(node):
    """Return a stable AST representation while ignoring only the test name."""
    copied = ast.parse(ast.unparse(node)).body[0]
    copied.name = "test"
    return ast.dump(copied, include_attributes=False)


def _test_functions():
    for path in sorted(TEST_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    yield path, node


def test_test_suite_contains_no_exact_duplicate_tests():
    duplicates = defaultdict(list)

    for path, node in _test_functions():
        duplicates[_normalised_test_ast(node)].append(
            "{}:{}:{}".format(
                path.relative_to(TEST_ROOT.parent),
                node.lineno,
                node.name,
            )
        )

    duplicate_groups = [
        locations
        for locations in duplicates.values()
        if len(locations) > 1
    ]

    assert duplicate_groups == [], (
        "Exact duplicate test bodies found:\n{}".format(
            "\n\n".join(
                "\n".join(group)
                for group in duplicate_groups
            )
        )
    )
