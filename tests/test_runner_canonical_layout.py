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
    assert 'ReadWritePaths=/var/lib/journeyman/remote-runner ' in unit
    for name in ('journeyman-runner', 'journeyman-environment-builder'):
        control_unit = (ROOT / ('deploy/systemd/' + name + '.service')).read_text()
        assert 'ExecStart=/opt/journeyman/venv/bin/python3 ' in control_unit


def test_migration_script_compiles():
    result = subprocess.run(['python3', '-m', 'py_compile', str(MIGRATION)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_migrates_legacy_named_snmp_file_with_identical_canonical_copy(tmp_path, monkeypatch):
    migration, _ = fixture_layout(tmp_path, monkeypatch)
    legacy_config = register(migration)
    legacy_snmp = migration.CONFIG_DIR / 'snmp-sources-benupd19.json'
    canonical_snmp = migration.DIR_MAPPINGS['JOURNEYMAN_SNMP_SOURCES_FILE']
    legacy_snmp.write_text('[]\n')
    canonical_snmp.write_text('[]\n')
    legacy_config.write_text(
        legacy_config.read_text() +
        'JOURNEYMAN_SNMP_SOURCES_FILE=' + str(legacy_snmp) + '\n'
    )
    assert migration.plan('uuid-A')[0] == legacy_config
    migration.apply('uuid-A')
    assert canonical_snmp.read_text() == '[]\n'
    assert legacy_snmp.read_text() == '[]\n'
    assert 'JOURNEYMAN_SNMP_SOURCES_FILE=' + str(canonical_snmp) in migration.CANONICAL_CONFIG.read_text()


def test_refuses_conflicting_named_snmp_files(tmp_path, monkeypatch):
    migration, _ = fixture_layout(tmp_path, monkeypatch)
    legacy_config = register(migration)
    legacy_snmp = migration.CONFIG_DIR / 'snmp-sources-benupd19.json'
    canonical_snmp = migration.DIR_MAPPINGS['JOURNEYMAN_SNMP_SOURCES_FILE']
    legacy_snmp.write_text('["old"]\n')
    canonical_snmp.write_text('["new"]\n')
    legacy_config.write_text(
        legacy_config.read_text() +
        'JOURNEYMAN_SNMP_SOURCES_FILE=' + str(legacy_snmp) + '\n'
    )
    with pytest.raises(migration.MigrationError, match='contain data'):
        migration.plan('uuid-A')


def test_migration_repairs_relocated_ansible_entrypoints(tmp_path, monkeypatch):
    migration, _ = fixture_layout(tmp_path, monkeypatch)
    register(migration)
    canonical = migration.DIR_MAPPINGS['JOURNEYMAN_ENVIRONMENT_ROOT']
    previous = canonical.with_name('environments-benupd19')
    old_bin = previous / '4-modern-ansible' / 'bin'
    old_bin.mkdir(parents=True)
    playbook = old_bin / 'ansible-playbook'
    playbook.write_bytes(
        ('#!' + str(old_bin / 'python') + '\n').encode() + b'print("test")\n'
    )
    playbook.chmod(0o755)
    other = old_bin / 'unrelated'
    other.write_text('#!/usr/bin/python3\nprint("untouched")\n')

    migration.apply('uuid-A')
    new_bin = canonical / '4-modern-ansible' / 'bin'
    assert (new_bin / 'ansible-playbook').read_bytes().startswith(
        ('#!' + str(new_bin / 'python') + '\n').encode()
    )
    assert (new_bin / 'unrelated').read_text() == '#!/usr/bin/python3\nprint("untouched")\n'
    assert (new_bin / 'ansible-playbook').stat().st_mode & 0o111
    assert not previous.exists()
    # Idempotent if the same directory is inspected again.
    assert migration.repair_relocated_environment_entrypoints(previous, canonical) == 0


def test_runner_installers_create_writable_completion_spool():
    root = ROOT
    assert '/var/lib/journeyman/remote-runner/completions' in (
        root / 'deploy/ansible/manage-remote-runner.yml'
    ).read_text()
    assert '/var/lib/journeyman/remote-runner/completions' in (
        root / 'deploy/ansible/install-remote-runner.yml'
    ).read_text()
    bootstrap = (root / 'app/services/runner_bootstrap.py').read_text()
    assert bootstrap.count(
        'install -d -o journeyman -g journeyman -m 0700 /var/lib/journeyman/remote-runner/completions'
    ) >= 2
