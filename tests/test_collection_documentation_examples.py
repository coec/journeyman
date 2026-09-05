"""Regression checks for the public Ansible collection examples."""

import ast
from pathlib import Path

import yaml


COLLECTION_MODULE_DIRS = (
    Path("ansible_collections/journeyman/configuration/plugins/modules"),
    Path("ansible_collections/journeyman/operation/plugins/modules"),
)


def _literal_assignment(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} does not define {name}")


def _module_files():
    for directory in COLLECTION_MODULE_DIRS:
        yield from sorted(directory.glob("*.py"))


def _module_option_keys(examples, fqcn):
    keys = set()
    for task in examples:
        if not isinstance(task, dict):
            continue
        value = task.get(fqcn)
        if isinstance(value, dict):
            keys.update(value)
    return keys


def test_collection_examples_are_valid_yaml_and_cover_every_documented_option():
    failures = []

    for path in _module_files():
        documentation = yaml.safe_load(_literal_assignment(path, "DOCUMENTATION"))
        examples = yaml.safe_load(_literal_assignment(path, "EXAMPLES"))

        assert isinstance(documentation, dict), path
        assert isinstance(examples, list), path

        collection = path.parts[2]
        fqcn = f"journeyman.{collection}.{path.stem}"
        documented = set((documentation.get("options") or {}).keys())
        exercised = _module_option_keys(examples, fqcn)
        missing = sorted(documented - exercised)
        if missing:
            failures.append(f"{fqcn}: {', '.join(missing)}")

    assert not failures, "Options missing from EXAMPLES:\n" + "\n".join(failures)
