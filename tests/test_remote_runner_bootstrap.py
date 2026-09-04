import importlib.util
from importlib.machinery import SourceFileLoader
import json
import stat
from pathlib import Path


def _load_remote_runner():
    path = Path(__file__).resolve().parents[1] / "bin" / "journeyman-remote-runner"
    loader = SourceFileLoader("journeyman_remote_runner", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remote_runner_local_script_execution_honours_shebang(tmp_path):
    runner = _load_remote_runner()
    script = tmp_path / "check.py"
    script.write_text(
        "#!/usr/bin/python3\nprint('ok')\n",
        encoding="utf-8",
    )

    assert runner.script_command(script) == [
        "/usr/bin/python3",
        str(script),
    ]


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_register_remote_runner_writes_protected_environment(tmp_path, monkeypatch):
    runner = _load_remote_runner()
    captured = {}

    def fake_urlopen(request, timeout, context):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({
            "runner_uuid": "runner-uuid",
            "runner_secret": "runner-secret",
            "name": "kunrun01",
            "site": "kununurra",
        })

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runner, "_ssl_context", lambda ca_file="": object())

    config = tmp_path / "remote-runner.env"
    result = runner.register_remote_runner(
        "https://journeyman.example/",
        "one-time-token",
        config_path=config,
        work_root="/var/lib/journeyman/remote-jobs",
    )

    assert captured["url"] == "https://journeyman.example/api/runners/register"
    assert captured["payload"]["token"] == "one-time-token"
    assert result["runner_uuid"] == "runner-uuid"
    assert result["name"] == "kunrun01"

    content = config.read_text()
    assert "JOURNEYMAN_SERVER_URL=https://journeyman.example" in content
    assert "JOURNEYMAN_RUNNER_UUID=runner-uuid" in content
    assert "JOURNEYMAN_RUNNER_SECRET=runner-secret" in content
    assert "JOURNEYMAN_REMOTE_WORK_ROOT=/var/lib/journeyman/remote-jobs" in content
    assert "JOURNEYMAN_SIGNAL_SPOOL_ROOT=/var/spool/journeyman/signals" in content
    assert "one-time-token" not in content
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_register_remote_runner_supports_isolated_same_host_instance_paths(tmp_path, monkeypatch):
    runner = _load_remote_runner()

    monkeypatch.setattr(
        runner.urllib.request,
        "urlopen",
        lambda request, timeout, context: _Response({
            "runner_uuid": "runner-uuid-2",
            "runner_secret": "runner-secret-2",
            "name": "dev-runner-2",
            "site": "development",
        }),
    )
    monkeypatch.setattr(runner, "_ssl_context", lambda ca_file="": object())

    config = tmp_path / "remote-runner-dev2.env"
    runner.register_remote_runner(
        "https://journeyman.example",
        "one-time-token",
        config_path=config,
        work_root="/var/lib/journeyman/remote-jobs-dev2",
        signal_spool_root="/var/spool/journeyman/signals-dev2",
    )

    content = config.read_text()
    assert "JOURNEYMAN_REMOTE_WORK_ROOT=/var/lib/journeyman/remote-jobs-dev2" in content
    assert "JOURNEYMAN_SIGNAL_SPOOL_ROOT=/var/spool/journeyman/signals-dev2" in content


def test_unregister_remote_runner_uses_credentials_from_environment_file(tmp_path, monkeypatch):
    runner = _load_remote_runner()
    captured = {}

    def fake_urlopen(request, timeout, context):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"status": "deleted", "name": "kunrun01"})

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runner, "_ssl_context", lambda ca_file="": object())

    config = tmp_path / "remote-runner.env"
    config.write_text(
        "JOURNEYMAN_SERVER_URL=https://journeyman.example\n"
        "JOURNEYMAN_RUNNER_UUID=runner-uuid\n"
        "JOURNEYMAN_RUNNER_SECRET=runner-secret\n"
    )

    result = runner.unregister_remote_runner(config, delete=True)

    assert captured["url"] == "https://journeyman.example/api/runners/unregister"
    assert captured["headers"]["X-journeyman-runner-id"] == "runner-uuid"
    assert captured["headers"]["Authorization"] == "Bearer runner-secret"
    assert captured["payload"] == {"delete": True}
    assert result["status"] == "deleted"


def test_systemd_template_supports_multiple_remote_runner_instances():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy" / "systemd" / "journeyman-remote-runner@.service").read_text()

    assert "EnvironmentFile=/etc/journeyman/remote-runner-%i.env" in unit
    assert "Description=Journeyman Remote Job Runner (%i)" in unit
    assert "ProtectSystem=full" in unit
    assert "ReadWritePaths=" not in unit


def test_builtin_management_separates_logical_runner_name_from_ssh_target():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()

    assert 'delegate_to: "{{ journeyman_runner_host }}"' in playbook
    assert '- --name\n          - "{{ journeyman_runner_name }}"' in playbook
    assert '- --runner\n          - "{{ journeyman_runner_name }}"' in playbook
    assert "remote-runner-' ~ journeyman_runner_name ~ '.env'" in playbook
    assert "journeyman-remote-runner@' ~ journeyman_runner_name" in playbook
    assert '- --signal-spool-root' in playbook
    assert 'when: not (journeyman_runner_named_instance | bool)' in playbook


def test_prepare_install_does_not_reuse_same_host_for_explicit_logical_name():
    root = Path(__file__).resolve().parents[1]
    admin = (root / "bin" / "journeyman-runner-admin").read_text()

    assert "if runner is None and name == host:" in admin

def test_runner_control_plane_values_are_resolved_at_point_of_consumption():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()

    assert "journeyman_capability_packages_raw" not in playbook
    assert "journeyman_receiver_config_raw" not in playbook
    assert "journeyman_runner_capability_packages" not in playbook
    assert "journeyman_snmp_sources:" not in playbook
    assert "lookup(" in playbook
    assert "ansible.builtin.pipe" in playbook
    assert "journeyman-runner-admin capability-packages --runner" in playbook
    assert "journeyman-runner-admin receiver-config --runner" in playbook
    assert "journeyman_required_runner_packages" not in playbook
    assert "journeyman_desired_snmp_sources" in playbook


def test_builtin_management_persists_credential_references_on_prepare():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()
    admin = (root / "bin" / "journeyman-runner-admin").read_text()

    remote_block = playbook.index("- name: Install or update remote runner")
    prepare_install = playbook.index("- name: Prepare runner record and one-time token for installation")
    prepare_update = playbook.index("- name: Persist runner management credential references for update")

    assert prepare_install < remote_block
    assert prepare_update < remote_block
    assert "--bootstrap-credential-id" in playbook[prepare_install:remote_block]
    assert "journeyman_bootstrap_credential_id" in playbook[prepare_install:remote_block]
    prepare_install_block = playbook[prepare_install:prepare_update]
    prepare_update_end = playbook.index(
        "- name: Preflight unregister/delete before optional remote cleanup",
        prepare_update,
    )
    prepare_update_block = playbook[prepare_update:prepare_update_end]
    assert "--pip-proxy-credential-id" in playbook[prepare_install:remote_block]
    assert "journeyman_pip_proxy_credential_id" in playbook[prepare_install:remote_block]
    assert "journeyman_pip_proxy_url" not in prepare_install_block
    assert "journeyman_pip_proxy_url" not in prepare_update_block
    assert '"prepare-update"' in admin
    assert "runner.management_bootstrap_credential_id = bootstrap_id" in admin
    assert "runner.management_pip_proxy_required = proxy_required" in admin
    assert "runner.management_pip_proxy_credential_id = proxy_id" in admin


def test_named_runner_snmp_receivers_are_host_scoped_but_fingerprints_are_instance_scoped():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()
    remote_runner = (root / "bin" / "journeyman-remote-runner").read_text()
    admin = (root / "bin" / "journeyman-runner-admin").read_text()

    assert "journeyman_snmp_sources_file" in playbook
    assert "snmp-sources-' ~ journeyman_runner_name ~ '.json'" in playbook
    assert "JOURNEYMAN_SNMP_SOURCES_FILE={{ journeyman_snmp_sources_file }}" in playbook
    assert ").host_snmp_sources" in playbook
    assert "Environment=JOURNEYMAN_SIGNAL_SPOOL_ROOT={{ item.signal_spool_root }}" in playbook
    assert "ReadWritePaths={{ item.signal_spool_root }}" in playbook
    assert 'SNMP_SOURCES_FILE = Path(env("JOURNEYMAN_SNMP_SOURCES_FILE"' in remote_runner
    assert '"host_snmp_sources": snmp_host_configuration(runner)' in admin

    snmp_section = playbook.split("- name: Store desired SNMP Source configuration", 1)[1].split(
        "- name: Install Journeyman remote runner systemd unit", 1
    )[0]
    assert "when: not (journeyman_runner_named_instance | bool)" not in snmp_section


def test_runner_management_keeps_ansible_221_bootstrap_workarounds():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()

    assert "ansible.builtin.dnf:" in playbook
    assert "journeyman_runner_venv ~ '/bin/pip'" in playbook
    assert "ansible.builtin.pip:" not in playbook


def test_update_repairs_missing_registration_before_remote_mutation():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()
    admin = (root / "bin" / "journeyman-runner-admin").read_text()

    registration_preflight = playbook.index(
        "- name: Check existing runner registration before update changes"
    )
    remote_mutation = playbook.index("- name: Install or update remote runner")
    recovery_prepare = playbook.index(
        "- name: Prepare one-time recovery token for missing update registration"
    )
    recovery_register = playbook.index(
        "- name: Repair missing runner registration during update"
    )

    assert registration_preflight < recovery_prepare < remote_mutation < recovery_register
    assert "journeyman-runner-admin\n          - prepare-recovery" in playbook
    assert "journeyman_recovery_registration_token" in playbook
    assert "Require existing registration for update" not in playbook
    assert '"prepare-recovery"' in admin
    assert '"runner.prepare_recovery.builtin"' in admin
    assert '"registration_token": token' in admin


def test_remote_runner_environment_sync_has_writable_runner_local_root():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()
    legacy_unit = (root / "deploy" / "systemd" / "journeyman-remote-runner.service").read_text()
    instance_unit = (root / "deploy" / "systemd" / "journeyman-remote-runner@.service").read_text()
    remote_runner = (root / "bin" / "journeyman-remote-runner").read_text()

    assert "journeyman_runner_environment_root" in playbook
    assert "JOURNEYMAN_ENVIRONMENT_ROOT={{ journeyman_runner_environment_root }}" in playbook
    assert "/opt/journeyman/environments" in playbook
    assert "ProtectSystem=full" in legacy_unit
    assert "ProtectSystem=full" in instance_unit
    assert "ReadWritePaths=" not in legacy_unit
    assert "ReadWritePaths=" not in instance_unit
    assert "ProtectSystem=full" in playbook
    assert (
        "ReadWritePaths=/var/lib/journeyman/remote-jobs "
        "/var/spool/journeyman/signals /opt/journeyman/environments"
        not in playbook
    )
    assert (
        "ReadWritePaths=/var/lib/journeyman /var/spool/journeyman "
        "/opt/journeyman/environments-%i"
        not in playbook
    )
    assert 'api("/api/runners/environments/claim")' in remote_runner
    assert "synchronize_execution_environment" in remote_runner
    assert 'VERSION = "0.16"' in remote_runner


def test_environment_sync_runner_api_endpoints_bypass_interactive_login():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    auth_source = (root / "app" / "auth.py").read_text(encoding="utf-8")

    assert '"main.runner_environment_sync_claim_api"' in auth_source
    assert '"main.runner_environment_sync_complete_api"' in auth_source


def test_remote_runner_environment_sync_failure_does_not_block_heartbeat_or_job_poll():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    remote_runner = (
        root / "bin" / "journeyman-remote-runner"
    ).read_text(encoding="utf-8")

    assert 'LOGGER.exception("Environment synchronization poll failed.")' in remote_runner
    assert "returned HTTP {} with non-JSON response" in remote_runner
    assert 'api("/api/runners/jobs/claim")' in remote_runner


def test_environment_sync_uses_writable_ansible_runtime_under_environment_root():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    remote_runner = (
        root / "bin" / "journeyman-remote-runner"
    ).read_text(encoding="utf-8")

    assert 'sync_ansible_home = ENVIRONMENT_ROOT / ".ansible-sync-{}".format(environment_id)' in remote_runner
    assert 'runtime_environment["ANSIBLE_HOME"] = str(sync_ansible_home)' in remote_runner
    assert 'runtime_environment["ANSIBLE_LOCAL_TEMP"] = str(sync_ansible_tmp)' in remote_runner
    assert 'runtime_environment["ANSIBLE_SSH_CONTROL_PATH_DIR"] = str(sync_ansible_cp)' in remote_runner


def test_environment_sync_accepts_ansible_patch_drift_within_release_series():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    service_source = (root / "app" / "services" / "runner_environment_sync.py").read_text(encoding="utf-8")
    remote_runner = (root / "bin" / "journeyman-remote-runner").read_text(encoding="utf-8")

    assert 'return "ansible-core>={}.{},<{}.{}".format(major, minor, major, minor + 1)' in service_source
    assert 'payload.get("ansible_compatibility_requirement") or ""' in remote_runner
    assert "actual_ansible_series == expected_ansible_series" in remote_runner
    assert "Ansible major.minor release does not match" in remote_runner


def test_remote_runner_environment_sync_checks_system_packages_and_disables_pip_cache():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    remote_runner = (
        root / "bin" / "journeyman-remote-runner"
    ).read_text(encoding="utf-8")
    manage_playbook = (
        root / "deploy" / "ansible" / "manage-remote-runner.yml"
    ).read_text(encoding="utf-8")
    admin_helper = (
        root / "bin" / "journeyman-runner-admin"
    ).read_text(encoding="utf-8")

    assert 'payload.get("system_requirements") or []' in remote_runner
    assert "Required runner system packages are not installed" in remote_runner
    assert 'runtime_environment["PIP_NO_CACHE_DIR"] = "1"' in remote_runner
    assert "Install required execution Environment system packages" in manage_playbook
    assert "environment-packages --runner" in manage_playbook
    assert '"environment-packages"' in admin_helper


def test_remote_runner_uses_writable_posix_remote_tmp_for_local_delegation():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    remote_runner = (
        root / "bin" / "journeyman-remote-runner"
    ).read_text(encoding="utf-8")

    assert 'result["ANSIBLE_REMOTE_TEMP"] = remote_temp' in remote_runner
    assert 'result["ANSIBLE_REMOTE_TMP"] = remote_temp' in remote_runner
    assert '"/tmp/.ansible-journeyman-{}".format(' in remote_runner


def test_remote_runner_heartbeat_applies_server_reported_capacity(monkeypatch, tmp_path):
    runner = _load_remote_runner()
    runner.WORK_ROOT = tmp_path

    def fake_request(method, url, *, token=None, payload=None, binary=False):
        assert method == "POST"
        assert url.endswith("/api/runners/heartbeat")
        assert payload["running_steps"] == 0
        return 200, {
            "status": "accepted",
            "enabled": True,
            "max_concurrent_steps": 4,
        }

    monkeypatch.setattr(runner, "request", fake_request)
    monkeypatch.setattr(runner, "managed_capability_status", lambda: {})
    monkeypatch.setattr(runner, "execution_environment_status", lambda: [])

    runner.heartbeat()

    assert runner.worker_capacity() == 4
    assert runner.available_worker_slots() == 4


def test_remote_runner_worker_pool_uses_all_reported_capacity(monkeypatch):
    import threading
    import time

    runner = _load_remote_runner()
    runner._set_worker_capacity(4)
    release = threading.Event()
    all_started = threading.Event()
    started = []
    started_lock = threading.Lock()

    def fake_execute(manifest):
        with started_lock:
            started.append(manifest["job_id"])
            if len(started) == 4:
                all_started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(runner, "execute_job", fake_execute)
    monkeypatch.setattr(runner, "heartbeat", lambda *args, **kwargs: None)

    for job_id in range(1, 5):
        assert runner.start_assignment({"job_id": job_id}) is True

    assert all_started.wait(timeout=2)
    assert runner.active_assignment_count() == 4
    assert runner.available_worker_slots() == 0
    assert runner.start_assignment({"job_id": 5}) is False

    release.set()
    deadline = time.monotonic() + 2
    while runner.active_assignment_count() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert runner.active_assignment_count() == 0
    assert runner.available_worker_slots() == 4


def test_remote_runner_heartbeat_loop_runs_while_assignment_is_active(monkeypatch):
    import threading

    runner = _load_remote_runner()
    runner.STOP = False
    runner.HEARTBEAT_SECONDS = 5
    runner._set_worker_capacity(1)
    release = threading.Event()
    started = threading.Event()
    heartbeat_counts = []

    def fake_execute(_manifest):
        started.set()
        release.wait(timeout=5)

    def fake_heartbeat(*args, **kwargs):
        heartbeat_counts.append(runner.active_assignment_count())
        runner.STOP = True

    monkeypatch.setattr(runner, "execute_job", fake_execute)
    monkeypatch.setattr(runner, "heartbeat", fake_heartbeat)

    assert runner.start_assignment({"job_id": 42}) is True
    assert started.wait(timeout=2)

    runner.remote_runner_heartbeat_loop()

    assert heartbeat_counts == [1]
    release.set()
    runner.wait_for_assignments()


def test_ansible_execution_records_final_per_host_results_for_failed_only_reruns():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    builtin_runner = (root / "bin" / "journeyman-runner").read_text(encoding="utf-8")
    remote_runner = (root / "bin" / "journeyman-remote-runner").read_text(encoding="utf-8")

    for source in (builtin_runner, remote_runner):
        assert 'summary.get("unreachable")' in source
        assert 'summary.get("failures")' in source
        assert '"JOURNEYMAN_REMOTE_SHELL_RESULTS_FILE"' in source
        assert '"JOURNEYMAN_HOST_RESULTS_STATUS_ONLY"' in source

    assert 'execution_type in {"ansible", "remote_shell"}' in builtin_runner
    assert 'job.execution_type in {"ansible", "remote_shell"}' in builtin_runner
    assert 'manifest["execution_type"] in {"ansible", "remote_shell"}' in remote_runner


def test_update_reconciles_legacy_and_named_systemd_layouts_by_runner_uuid():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()
    admin = (root / "bin" / "journeyman-runner-admin").read_text()

    assert '"runner_uuid": runner.runner_uuid' in admin
    assert "register: journeyman_update_prepare" in playbook
    assert "journeyman_expected_runner_uuid" in playbook
    assert "Find existing remote runner registration files before update" in playbook
    assert "Read existing remote runner registrations before update" in playbook
    assert "Identify obsolete systemd layouts for this runner registration" in playbook
    assert "item.source != journeyman_runner_config" in playbook
    assert "'JOURNEYMAN_RUNNER_UUID=' ~ journeyman_expected_runner_uuid" in playbook
    assert "Stop and disable obsolete remote runner systemd layout" in playbook
    assert "Verify obsolete remote runner systemd layout is inactive" in playbook
    assert "Verify obsolete remote runner systemd layout is disabled" in playbook
    assert "Verify obsolete remote runner process exited before update" in playbook
    assert "Remove obsolete runner registration after successful layout transition" in playbook

    identify = playbook.index("- name: Identify obsolete systemd layouts for this runner registration")
    stop = playbook.index("- name: Stop and disable obsolete remote runner systemd layout")
    mutate = playbook.index("- name: Install or update remote runner")
    verify_active = playbook.index("- name: Verify Journeyman remote runner is active")
    cleanup = playbook.index("- name: Remove obsolete runner registration after successful layout transition")

    assert identify < stop < mutate < verify_active < cleanup


def test_update_layout_reconciliation_does_not_blindly_stop_other_named_runners():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text()

    reconciliation = playbook.split(
        "- name: Identify obsolete systemd layouts for this runner registration", 1
    )[1].split("# Check the registration file before Update changes any software", 1)[0]

    assert "item.source != journeyman_runner_config" in reconciliation
    assert "journeyman_expected_runner_uuid" in reconciliation
    assert "b64decode" in reconciliation
    assert "remote-runner-*.env" not in reconciliation


def test_remote_runner_reports_only_server_declared_runtime_dependencies(monkeypatch, tmp_path):
    runner = _load_remote_runner()
    runner.WORK_ROOT = tmp_path
    reported = []

    def fake_version(name):
        versions = {
            "ansible-core": "2.21.3",
            "cryptography": "50.0.0",
            "packaging": "26.2",
        }
        if name not in versions:
            raise runner.importlib_metadata.PackageNotFoundError(name)
        return versions[name]

    def fake_request(method, url, *, token=None, payload=None, binary=False):
        reported.append(payload["runtime_dependencies"])
        return 200, {
            "status": "accepted",
            "enabled": True,
            "max_concurrent_steps": 1,
            "runtime_dependency_names": [
                "ansible-core",
                "cryptography",
                "packaging",
            ],
        }

    monkeypatch.setattr(runner.importlib_metadata, "version", fake_version)
    monkeypatch.setattr(runner, "request", fake_request)
    monkeypatch.setattr(runner, "managed_capability_status", lambda: {})
    monkeypatch.setattr(runner, "execution_environment_status", lambda: [])

    # The first heartbeat reports the bootstrap roots. The control plane then
    # supplies the canonical closure for subsequent heartbeats.
    runner.heartbeat()
    runner.heartbeat()

    assert set(reported[0]).issubset({"cryptography"})
    assert reported[1] == {
        "ansible-core": "2.21.3",
        "cryptography": "50.0.0",
        "packaging": "26.2",
    }


def test_manage_remote_runner_reconciles_exact_control_plane_runtime_requirements():
    root = Path(__file__).resolve().parents[1]
    playbook = (root / "deploy" / "ansible" / "manage-remote-runner.yml").read_text(
        encoding="utf-8"
    )
    admin = (root / "bin" / "journeyman-runner-admin").read_text(encoding="utf-8")

    assert "journeyman_runner_runtime_requirements" in playbook
    assert "journeyman-runner-admin runtime-dependencies" in playbook
    assert "Reconcile remote runner Python runtime dependencies" in playbook
    assert "+ journeyman_runner_runtime_requirements" in playbook
    assert '"runtime-dependencies"' in admin
    assert "canonical_runner_runtime_requirements" in admin


def test_remote_runner_install_preflights_journeyman_tls_trust_before_mutation():
    root = Path(__file__).resolve().parents[1]
    manage_playbook = (
        root / "deploy" / "ansible" / "manage-remote-runner.yml"
    ).read_text(encoding="utf-8")
    install_playbook = (
        root / "deploy" / "ansible" / "install-remote-runner.yml"
    ).read_text(encoding="utf-8")

    for playbook in (manage_playbook, install_playbook):
        ssh = playbook.index("- name: Verify SSH authentication to remote runner")
        sudo = playbook.index("- name: Verify privilege escalation on remote runner")
        check = playbook.index("- name: Check Journeyman HTTPS trust from remote runner")
        require = playbook.index("- name: Require Journeyman HTTPS trust from remote runner")
        mutate = playbook.index("- name: Create Journeyman runner group")

        assert ssh < sudo < check < require < mutate
        preflight_section = playbook[ssh:check]
        assert "ansible.builtin.raw: /usr/bin/true" in preflight_section
        assert "become: false" in preflight_section
        assert "become: true" in preflight_section
        assert preflight_section.count("changed_when: false") >= 2

        tls_section = playbook[check:mutate]
        assert "ansible.builtin.uri:" in tls_section
        assert "/api/runners/register" in tls_section
        assert "validate_certs: true" in tls_section
        assert "journeyman_runner_ca_file" in tls_section
        assert "failed_when: false" in tls_section
        assert "journeyman_runner_tls_preflight.status" in tls_section
        assert "trusts the CA that issued Journeyman's TLS certificate" in tls_section
        assert "Underlying error:" in tls_section
