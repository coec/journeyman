from pathlib import Path
import ast
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _literal_assignment(path, name):
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError("{} is missing {}".format(path, name))


def test_ansible_module_documentation_contract():
    module_paths = sorted(
        (ROOT / "ansible_collections" / "journeyman").glob("*/plugins/modules/*.py")
    )
    assert module_paths

    for path in module_paths:
        documentation = yaml.safe_load(_literal_assignment(path, "DOCUMENTATION"))
        assert documentation["module"] == path.stem
        assert documentation["short_description"]
        assert documentation["description"]
        assert documentation["version_added"] == "0.1.0"
        assert documentation["author"]
        for option_name, option in documentation.get("options", {}).items():
            assert option.get("description"), "{} option {} lacks description".format(
                path, option_name
            )

        examples = _literal_assignment(path, "EXAMPLES")
        assert examples.strip()

        returns = yaml.safe_load(_literal_assignment(path, "RETURN")) or {}
        for return_name, return_value in returns.items():
            assert return_value.get("description"), "{} return {} lacks description".format(
                path, return_name
            )
            assert return_value.get("returned"), "{} return {} lacks returned".format(
                path, return_name
            )
            assert return_value.get("type"), "{} return {} lacks type".format(
                path, return_name
            )
