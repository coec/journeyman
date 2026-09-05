# Managed Linux/UNIX Hosts

This guide describes the minimum setup required on a Linux or UNIX host so that Journeyman can run Ansible playbooks or scripts against it.

The examples below use a dedicated service account named `svc_journeyman`. You may use a different account name, but the same requirements apply.

## How the connection works

Journeyman does not normally log in to managed hosts directly from the web application.

The execution path is:

```text
Journeyman
    |
    | dispatches a Job
    v
Journeyman runner
    |
    | SSH using the selected Machine credential
    v
managed host
    |
    | optional sudo / Ansible become
    v
privileged execution
```

The runner may be the built-in runner on the Journeyman server or a registered remote runner.

Whichever runner executes the Job must be able to resolve and reach the managed host over the required network path.

## Requirements

For a typical RHEL-family managed host, the target needs:

- OpenSSH server
- a dedicated login account
- the public half of the SSH key used by the Journeyman Machine credential
- Python 3 for normal Ansible module execution
- `sudo` if playbooks or scripts use privilege escalation
- suitable network and firewall access from the runner

A minimal RHEL installation can be prepared with:

```bash
dnf install -y openssh-server sudo python3
systemctl enable --now sshd
```

## 1. Create the service account

Create a dedicated account:

```bash
useradd -m -s /bin/bash svc_journeyman
```

Verify it:

```bash
getent passwd svc_journeyman
```

Example:

```text
svc_journeyman:x:1001:1001::/home/svc_journeyman:/bin/bash
```

The account requires a usable login shell because it is used for SSH-based execution.

## 2. Install the SSH public key

The private key belongs in Journeyman.

Only the corresponding public key needs to be installed on the managed host.

Create the SSH directory:

```bash
install -d \
  -m 0700 \
  -o svc_journeyman \
  -g svc_journeyman \
  /home/svc_journeyman/.ssh
```

Install the public key:

```bash
cat >/home/svc_journeyman/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAA...replace-with-your-public-key...
EOF
```

Set ownership and permissions:

```bash
chown svc_journeyman:svc_journeyman \
  /home/svc_journeyman/.ssh/authorized_keys

chmod 0600 \
  /home/svc_journeyman/.ssh/authorized_keys
```

Verify:

```bash
ls -ld /home/svc_journeyman/.ssh
ls -l /home/svc_journeyman/.ssh/authorized_keys
```

Expected permissions are:

```text
/home/svc_journeyman/.ssh                 0700
/home/svc_journeyman/.ssh/authorized_keys 0600
```

### Do not leave the private key on the managed host

The managed host does not need a copy of the private key.

The normal trust relationship is:

```text
Journeyman Machine credential
        contains private key
                |
                v
runner SSH client
                |
                v
managed host authorized_keys
        contains public key
```

If a private key was generated temporarily on the target while setting up the account, copy it into the Journeyman Machine credential and remove it from the target afterward.

## 3. Configure sudo

If Journeyman playbooks or scripts need root privileges, configure non-interactive sudo.

Create:

```text
/etc/sudoers.d/svc_journeyman
```

with:

```sudoers
svc_journeyman ALL=(ALL) NOPASSWD: ALL
```

Set the required permissions:

```bash
chmod 0440 /etc/sudoers.d/svc_journeyman
```

Validate the file:

```bash
visudo -cf /etc/sudoers.d/svc_journeyman
```

Test non-interactive privilege escalation:

```bash
sudo -u svc_journeyman sudo -n id
```

Expected result:

```text
uid=0(root) gid=0(root) groups=0(root)
```

A more restrictive sudo policy may be used if appropriate for your environment, but it must allow all commands required by the Journeyman Projects and Packages assigned to the host.

## 4. Confirm Python is available

Most Ansible modules require Python on the managed host.

Check:

```bash
python3 --version
```

Example:

```text
Python 3.9.25
```

Raw or shell-based automation can work without Python in some cases, but a normal Journeyman-managed Linux host should have Python 3 available.

## 5. Create the Journeyman Machine credential

In Journeyman, create or update a Machine credential with:

```text
Username:        svc_journeyman
SSH private key: private key matching authorized_keys
```

If sudo is configured as `NOPASSWD`, no become password is required.

Attach the Machine credential to the Project or Package step that will access the managed host.

## 6. Add the host to an Inventory

Add the managed host to a Journeyman Inventory, for example:

```text
client.local
```

The runner selected for the Job must be able to:

- resolve the hostname;
- route to the host;
- connect to SSH;
- reach any other services required by the automation.

This is especially important when using remote runners. A host reachable from the Journeyman server is not necessarily reachable from every remote runner.

## SSH host-key policy

Journeyman-managed SSH connections deliberately do not retain or validate SSH host keys.

Journeyman uses the equivalent of:

```text
StrictHostKeyChecking=no
UserKnownHostsFile=/dev/null
```

This means:

- unknown host keys are accepted;
- changed host keys do not block execution;
- host keys are not persisted to `known_hosts`.

This policy is useful in environments where managed nodes are frequently rebuilt and therefore receive new SSH host keys.

The trade-off is that SSH server identity is not cryptographically verified by host key. Administrators should ensure that the network and inventory sources provide the level of trust appropriate for their environment.

## Validate the managed host

A simple validation playbook can prove both ordinary SSH execution and privilege escalation:

```yaml
---
- name: Validate Journeyman managed host
  hosts: all
  gather_facts: false

  tasks:
    - name: Verify SSH identity
      ansible.builtin.command: id
      register: normal_identity
      changed_when: false

    - name: Show SSH identity
      ansible.builtin.debug:
        var: normal_identity.stdout

    - name: Verify privilege escalation
      become: true
      ansible.builtin.command: id
      register: privileged_identity
      changed_when: false

    - name: Show privileged identity
      ansible.builtin.debug:
        var: privileged_identity.stdout
```

The first `id` should report the configured service account, for example:

```text
uid=1001(svc_journeyman) ...
```

The second should report:

```text
uid=0(root) ...
```

## Quick checklist

Before troubleshooting Journeyman itself, verify:

```text
[ ] sshd is running
[ ] service account exists and has a login shell
[ ] ~/.ssh is mode 0700
[ ] authorized_keys is mode 0600
[ ] authorized_keys contains the public key matching the Journeyman credential
[ ] the private key is stored in Journeyman, not on the managed host
[ ] sudo -n works if become is required
[ ] python3 is installed
[ ] the selected runner can resolve and reach the managed host
[ ] the correct Machine credential is attached to the Project/Package
```

## Troubleshooting

### `Permission denied (publickey,...)`

Check that the private key in Journeyman matches the public key in `authorized_keys`.

If you still have the private key available on a secure administration host, derive its public key:

```bash
ssh-keygen -y -f id_ed25519
```

Compare it with:

```bash
cat /home/svc_journeyman/.ssh/authorized_keys
```

Also verify ownership and permissions on the home directory, `.ssh`, and `authorized_keys`.

### `sudo: a password is required`

Confirm the sudoers entry exists and validate it:

```bash
visudo -cf /etc/sudoers.d/svc_journeyman
sudo -u svc_journeyman sudo -n id
```

### Python-related Ansible failures

Verify:

```bash
python3 --version
```

If multiple Python versions are installed, set `ansible_python_interpreter` in the Inventory where required.

### Works from the built-in runner but not from a remote runner

Test connectivity from the runner that actually executes the Job.

For example, from the remote runner:

```bash
getent hosts client.local
```

and verify TCP/22 is reachable using the tools permitted in your environment.

The runner, not the Journeyman web server, makes the SSH connection to the managed host.

## Windows managed hosts

This document covers SSH-managed Linux/UNIX systems.

Windows hosts normally use WinRM or another Windows-specific Ansible connection method and have different credential and target-side requirements.

