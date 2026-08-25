from pathlib import Path

import pytest

pytestmark = pytest.mark.security
ROOT = Path(__file__).resolve().parents[2]


def test_top_level_service_is_only_enableable_main_server_unit():
    parent = (ROOT / 'deploy/systemd/journeyman.service').read_text(encoding='utf-8')
    assert 'WantedBy=multi-user.target' in parent
    assert 'journeyman-service-coordinator prepare' in parent

    for name in (
        'journeyman-web.service',
        'journeyman-scheduler.service',
        'journeyman-runner.service',
        'journeyman-environment-builder.service',
    ):
        unit = (ROOT / 'deploy/systemd' / name).read_text(encoding='utf-8')
        assert 'PartOf=journeyman.service' in unit
        assert 'WantedBy=multi-user.target' not in unit
        assert 'ExecReload=/bin/kill -HUP $MAINPID' in unit


def test_coordinator_enforces_disabled_children_and_runtime_hup_contract():
    script = (ROOT / 'bin/journeyman-service-coordinator').read_text(encoding='utf-8')
    for name in (
        'journeyman-web.service',
        'journeyman-scheduler.service',
        'journeyman-runner.service',
        'journeyman-environment-builder.service',
    ):
        assert name in script
    assert "_run_systemctl('disable', unit" in script
    assert "_run_systemctl('is-enabled', unit" in script
    assert "'--signal=HUP'" in script

    runner = (ROOT / 'bin/journeyman-runner').read_text(encoding='utf-8')
    builder = (ROOT / 'bin/journeyman-environment-builder').read_text(encoding='utf-8')
    scheduler = (ROOT / 'app/cli.py').read_text(encoding='utf-8')
    assert 'signal.SIGHUP' in runner
    assert 'signal.SIGHUP' in builder
    assert 'signal.SIGHUP' in scheduler
