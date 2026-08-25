from pathlib import Path

from app.routes import _validate_project_steps


def _incomplete_step():
    return {
        "name": "",
        "repository_id": None,
        "inventory_id": None,
        "environment_id": None,
        "credential_ids": [],
        "credentials_override": False,
        "playbook": "",
        "extra_vars_yaml": "",
        "verbosity": 0,
        "failure_only": False,
        "dependency_positions": [],
    }


def test_project_form_uses_project_defaults_and_step_tabs():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "project_form.html"
    ).read_text(encoding="utf-8")

    project_tab = template.index('data-project-tab="project"')
    defaults_tab = template.index('data-project-tab="defaults"')
    step_tabs = template.index('id="project-step-tabs"')

    assert project_tab < defaults_tab < step_tabs
    assert 'id="project-defaults-panel"' in template
    assert 'id="add-step"' in template
    assert "function updateStepTabs()" in template
    assert "activateStepTab(newStep);" in template
    assert 'defaultsTabButton.addEventListener("click", activateDefaultsTab);' in template

    defaults_panel = template.split(
        '<div id="project-defaults-panel"', 1
    )[1].split('<div id="workflow-steps"', 1)[0]
    assert """            Credentials
            <select name="credential_ids""" in defaults_panel
    assert """            Repository
            <select name="repository_id""" in defaults_panel
    assert """            Execution Environment
            <select name="environment_id""" in defaults_panel
    assert "Default Credentials" not in defaults_panel
    assert "Default Repository" not in defaults_panel
    assert "Default Execution Environment" not in defaults_panel


def test_incomplete_step_is_saveable_but_not_dispatch_valid(app):
    row = _incomplete_step()

    with app.app_context():
        save_errors = _validate_project_steps(
            [dict(row)],
            {},
            set(),
            dispatch_validation=False,
        )
        dispatch_errors = _validate_project_steps(
            [dict(row)],
            {},
            set(),
            dispatch_validation=True,
        )

    assert save_errors == []
    assert "Step 1 requires a valid repository." in dispatch_errors
    assert "Step 1 requires an Ansible YAML file." in dispatch_errors
