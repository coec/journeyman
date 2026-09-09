from pathlib import Path


def test_execution_type_ui_updates_each_step_with_scoped_step_reference():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "project_form.html"
    ).read_text(encoding="utf-8")

    assert "function updateStepTypeUi(step)" in template
    assert 'step.querySelectorAll(".remote-shell-only-field")' in template
    assert (
        'document.querySelectorAll(".workflow-step").forEach(updateStepTypeUi);'
        in template
    )
    assert "populateAllPlaybookSelects();" in template
