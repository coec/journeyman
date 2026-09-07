# Journeyman examples

This repository contains small, deliberately harmless examples for exploring
Journeyman.

Suggested first tests:

1. Add a Static inventory named `Localhost`.
2. Copy the contents of `inventories/localhost.yml` into the inventory.
3. Create a Project using this disk repository.
4. Select one of the example playbooks or scripts.
5. Run it and inspect the resulting Job.

The localhost inventory does not require SSH or a Machine credential.

## Playbooks

* `playbooks/hello.yml` - prints a simple Journeyman greeting.
* `playbooks/ping.yml` - runs Ansible's ping module.
* `playbooks/facts.yml` - gathers and displays a few host facts.
* `playbooks/uptime.yml` - displays uptime without changing the host.

## Scripts

* `scripts/hello.sh` - simple script execution example.
* `scripts/show-environment.sh` - shows the execution context made available
  to a script.

## Safety

The supplied content is intentionally read-only with respect to the target
machine. It does not install packages, modify services, change files or require
privilege escalation.
