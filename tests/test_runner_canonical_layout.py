"""Host-scoped remote runner migration and packaging invariants."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts" / "journeyman-runner-layout"


def migration_module():
    loader = importlib.machinery.SourceFileLoader("journeyman_runner_layout_test", str(MIGRATION))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def fixture_layout(tmp_path, monkeypatch):
    migration = migration_module()
    etc = tmp_path / 'etc'
    etc.mkdir()
    work = tmp_path / 'lib'
    work.mkdir()
    spool = tmp_path / 'spool'
    spool.mkdir()
    envs = tmp_path / 'environments'
    envs.mkdir()
    monkeypatch.setattr(migration, 'CONFIG_DIR', etc)
    monkeypatch.setattr(migration, 'CANONICAL_CONFIG', etc / 'remote-runner.env')
    monkeypatch.setattr(migration, 'DIR_MAPPINGS', {
        'JOURNEYMAN_REMOTE_WORK_ROOT': work / 'remote-jobs',
        'JOURNEYMAN_SIGNAL_SPOOL_ROOT': spool / 'signals',
        'JOURNEYMAN_ENVIRONMENT_ROOT': envs / 'environments',
        'JOURNEYMAN_SNMP_SOURCES_FILE': etc / 'snmp-sources.json',
    })
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        if 'show' in command:
            # On this test host only the standard (misconfigured) service is installed.
            return type('Result', (), {'stdout': 'loaded\n' if command[-1] == 'journeyman-remote-runner.service' else 'not-found\n'})()
        return type('Result', (), {'stdout': ''})()
    monkeypatch.setattr(migration.subprocess, 'run', fake_run)
    return migration, commands


def register(migration, name='benupd19', uuid='uuid-A'):
    config = migration.CONFIG_DIR / ('remote-runner-' + name + '.env')
    config.write_text(
        'JOURNEYMAN_RUNNER_UUID=' + uuid + '\n' +
        'JOURNEYMAN_REMOTE_WORK_ROOT=' + str(migration.DIR_MAPPINGS['JOURNEYMAN_REMOTE_WORK_ROOT'].with_name('remote-jobs-' + name)) + '\n' +
        'JOURNEYMAN_SIGNAL_SPOOL_ROOT=' + str(migration.DIR_MAPPINGS['JOURNEYMAN_SIGNAL_SPOOL_ROOT'].with_name('signals-' + name)) + '\n' +
        'JOURNEYMAN_ENVIRONMENT_ROOT=' + str(migration.DIR_MAPPINGS['JOURNEYMAN_ENVIRONMENT_ROOT'].with_name('environments-' + name)) + '\n'
    )
    config.chmod(0o640)
    return config


def test_named_runner_moves_identity_and_data_without_rotating_pki(tmp_path, monkeypatch):
    migration, commands = fixture_layout(tmp_path, monkeypatch)
    old = register(migration)
    previous = migration.DIR_MAPPINGS['JOURNEYMAN_REMOTE_WORK_ROOT'].with_name('remote-jobs-benupd19')
    previous.mkdir()
    (previous / 'checkpoint').write_text('keep this')
    assert migration.plan('uuid-A')[0] == old
    assert 'Migrated ' in migration.apply('uuid-A')
    new = migration.CANONICAL_CONFIG
    assert new.is_file()
    assert not old.exists()
    assert (migration.DIR_MAPPINGS['JOURNEYMAN_REMOTE_WORK_ROOT'] / 'checkpoint').read_text() == 'keep this'
    content = new.read_text()
    assert 'JOURNEYMAN_RUNNER_UUID=uuid-A' in content
    assert 'remote-jobs-benupd19' not in content
    assert list(migration.CONFIG_DIR.glob('remote-runner-benupd19.env.migrated-*'))
    assert ['systemctl', 'show', '--property=LoadState', '--value',
            'journeyman-remote-runner@benupd19.service'] in commands
    assert ['systemctl', 'stop', 'journeyman-remote-runner.service'] in commands
    assert migration.plan('uuid-A')[0] == new


def test_refuses_multiple_runner_identities(tmp_path, monkeypatch):
    migration, commands = fixture_layout(tmp_path, monkeypatch)
    register(migration, 'a', 'uuid-A')
    register(migration, 'b', 'uuid-B')
    with pytest.raises(migration.MigrationError, match='Multiple'):
        migration.plan('uuid-A')
    assert not commands


def test_refuses_identity_mismatch(tmp_path, monkeypatch):
    migration, commands = fixture_layout(tmp_path, monkeypatch)
    old = register(migration, 'benupd19', 'uuid-wrong')
    with pytest.raises(migration.MigrationError, match='UUID mismatch'):
        migration.plan('uuid-expected')
    assert old.exists() and not commands


def test_refuses_conflicting_existing_data(tmp_path, monkeypatch):
    migration, commands = fixture_layout(tmp_path, monkeypatch)
    register(migration)
    src = migration.DIR_MAPPINGS['JOURNEYMAN_REMOTE_WORK_ROOT'].with_name('remote-jobs-benupd19')
    src.mkdir()
    (src / 'historical-job').write_text('source')
    dest = migration.DIR_MAPPINGS['JOURNEYMAN_REMOTE_WORK_ROOT']
    dest.mkdir()
    (dest / 'other-job').write_text('destination')
    with pytest.raises(migration.MigrationError, match='contain data'):
        migration.plan('uuid-A')
    assert not commands


def test_canonical_service_unit_and_interpreter_paths():
    unit = (ROOT / 'deploy/systemd/journeyman-remote-runner.service').read_text()
    assert 'EnvironmentFile=/etc/journeyman/remote-runner.env' in unit
    assert 'RuntimeDirectory=journeyman/ansible-cp' in unit
    assert 'ExecStart=/opt/journeyman/venv/bin/python3 ' in unit
    assert 'ProtectSystem=strict' in unit
    assert 'ReadWritePaths=' in unit
    for name in ('journeyman-runner', 'journeyman-environment-builder'):
        control_unit = (ROOT / ('deploy/systemd/' + name + '.service')).read_text()
        assert 'ExecStart=/opt/journeyman/venv/bin/python3 ' in control_unit


def test_migration_script_compiles():
    result = subprocess.run(['python3', '-m', 'py_compile', str(MIGRATION)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
