from pathlib import Path


def test_reactor_form_uses_five_editor_tabs():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "reactor_form.html"
    ).read_text(encoding="utf-8")

    for tab in ("Reactor", "Signal Matching", "Reaction Inputs", "Recovery Signal", "Safety"):
        assert f">{tab}</button>" in template

    assert 'data-reactor-tab="reactor"' in template
    assert 'data-reactor-tab="matching"' in template
    assert 'data-reactor-tab="inputs"' in template
    assert 'data-reactor-tab="recovery"' in template
    assert 'data-reactor-tab="safety"' in template
    assert 'id="reactor-tab-matching"' in template
    assert 'id="reactor-tab-inputs"' in template
    assert 'id="reactor-tab-recovery"' in template
    assert 'id="reactor-tab-safety"' in template


def test_recovery_controls_have_dedicated_tab_and_safety_stays_separate():
    template = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "templates"
        / "reactor_form.html"
    ).read_text(encoding="utf-8")

    recovery = template.split('id="reactor-tab-recovery"', 1)[1].split(
        'id="reactor-tab-safety"', 1
    )[0]
    safety = template.split('id="reactor-tab-safety"', 1)[1].split(
        '<div class="form-actions">', 1
    )[0]

    assert "Recovery Signal" in recovery
    assert 'name="recovery_window_seconds"' in recovery
    assert 'name="recovery_match_mode"' in recovery
    assert 'name="recovery_correlation_inputs"' in recovery
    assert 'name="cooldown_seconds"' not in recovery
    assert 'name="max_concurrency"' not in recovery

    assert 'name="cooldown_seconds"' in safety
    assert 'name="max_concurrency"' in safety
    assert 'name="recovery_window_seconds"' not in safety
