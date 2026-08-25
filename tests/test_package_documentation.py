from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_form_uses_runtime_variable_terminology():
    template = (
        ROOT
        / "app"
        / "templates"
        / "project_package_form.html"
    ).read_text(encoding="utf-8")

    assert "Runtime variable" in template
    assert "Ansible extra variable" not in template
    assert "Validation examples" in template


def test_readme_documents_package_as_user_input_layer():
    readme = (
        ROOT / "README.md"
    ).read_text(encoding="utf-8")

    assert "Packages define **how a person is allowed to dispatch a Project**" in readme
    assert "single user-input/survey layer" in readme
    assert "JOURNEYMAN_INPUT_*" in readme
