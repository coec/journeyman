from pathlib import Path


def test_project_form_uses_effective_default_repository_for_step_files():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "project_form.html"
    ).read_text()

    assert "function effectiveRepositoryId(step)" in template
    assert 'step.querySelector(".step-repository")' in template
    assert 'document.getElementById("project-default-repository")' in template
    assert 'event.target.matches("#project-default-repository")' in template
    assert "populatePlaybookSelect(playbookSelect)" in template
