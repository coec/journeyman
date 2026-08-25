from pathlib import Path


def test_execution_type_ui_does_not_reference_undefined_step():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "project_form.html"
    ).read_text(encoding="utf-8")

    assert (
        'step.querySelectorAll(".remote-shell-only-field")'
        not in template
    )
    assert (
        'document.querySelectorAll(".remote-shell-only-field")'
        in template
    )
    assert "populateAllPlaybookSelects();" in template
