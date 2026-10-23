"""Standalone regression: python tests/test_machine_credential_precedence.py.

Use real Ansible when available; no application/database bootstrap is needed.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_command_builder(name):
    # Load the actual command-building functions without starting either runner
    # or importing the control plane's application/database dependencies.
    path = ROOT / "bin" / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"machine_credential_command_files", "build_command"}]
    namespace = {"Path": Path, "json": json, "os": os}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["build_command"]


class MachineCredentialPrecedenceTests(unittest.TestCase):
    def test_linux_defaults_and_delegated_task_overrides(self):
        executable = shutil.which("ansible-playbook")
        if executable is None:
            self.skipTest("ansible-playbook is required for the integration regression")
        for name in ("journeyman-runner", "journeyman-remote-runner"):
            with self.subTest(runner=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                inventory = root / "hosts.yml"
                inventory.write_text("all:\n  hosts:\n    linux:\n      ansible_connection: local\n")
                machine = root / "machine.json"
                original = {"ansible_user": "svc-linux", "ansible_password": "linux-secret",
                            "custom_machine_value": "retained"}
                machine.write_text(json.dumps(original))
                windows = root / "windows.json"
                windows.write_text(json.dumps({"win_ansible_user": "svc-windows",
                                               "win_ansible_password": "windows-secret"}))
                playbook = root / "play.yml"
                playbook.write_text('''- hosts: all
  gather_facts: false
  tasks:
    - assert:
        that:
          - ansible_user == 'svc-linux'
          - ansible_password == 'linux-secret'
          - custom_machine_value == 'retained'
    - shell: "printf '%s:%s' '{{ ansible_user }}' '{{ ansible_password }}'"
      delegate_to: windows-outside-limit
      vars:
        ansible_connection: local
        ansible_user: "{{ win_ansible_user }}"
        ansible_password: "{{ win_ansible_password }}"
      register: delegated
    - assert:
        that: delegated.stdout == 'svc-windows:windows-secret'
- hosts: all
  gather_facts: false
  vars:
    ansible_user: play-user
  tasks:
    - assert:
        that: ansible_user == 'play-user'
''')
                builder = load_command_builder(name)
                step = dict(playbook="play.yml", verbosity=0, limit="linux", tags="",
                            skip_tags="", environment_path="__SYSTEM_ANSIBLE__")
                if name == "journeyman-runner":
                    command = builder(SimpleNamespace(**step), root, inventory,
                                      machine_extra_vars_path=machine,
                                      windows_extra_vars_path=windows)
                else:
                    command = builder(step, "ansible", root, inventory,
                                      machine_extra_vars_path=machine,
                                      windows_extra_vars_path=windows)
                self.assertNotIn("linux-secret", " ".join(command))
                self.assertEqual(json.loads(machine.read_text()), original)
                defaults = machine.with_suffix(".inventory.json")
                extras = machine.with_suffix(".extra-vars.json")
                self.assertEqual(defaults.stat().st_mode & 0o777, 0o600)
                self.assertEqual(extras.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(extras.read_text()),
                                 {"custom_machine_value": "retained"})
                command[0] = executable
                result = subprocess.run(command, text=True, capture_output=True,
                                        timeout=60, env={**os.environ, "ANSIBLE_NOCOLOR": "1"})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
