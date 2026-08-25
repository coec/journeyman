# Remote runner development topology

Journeyman remote runners poll the central Journeyman HTTPS API. They do not
require a dedicated execution listener port. For development and integration
testing, multiple independently registered remote runners may therefore run on
one Linux host.

This topology is useful for testing runner crews, least-busy selection,
per-runner assignment, concurrency, cancellation, lost-runner recovery and
runner capability/version reporting. It does **not** replace validation on
separate remote hosts: it cannot reproduce host failure, network partition,
different OS/package state, filesystem isolation, routing differences or
site-specific latency/failure modes.

## Create two development runners

Use the built-in **ZZ - Manage Remote Runner** Package twice. The Package now
separates the physical SSH destination from the logical Runner identity. For
example:

```text
Target host: rhel01.example.com
Runner name: dev-runner-1

Target host: rhel01.example.com
Runner name: dev-runner-2
```

When Runner name differs from Target host, Journeyman automatically uses the
same-host development layout: a systemd template instance plus unique
environment, work and Signal spool paths. The equivalent manual registration
layout is:

```bash
sudo /opt/journeyman/venv314/bin/python3 \
  /opt/journeyman/bin/journeyman-remote-runner register \
  --server https://journeyman.example \
  --token '<dev-runner-1-token>' \
  --config /etc/journeyman/remote-runner-dev1.env \
  --work-root /var/lib/journeyman/remote-jobs-dev1 \
  --signal-spool-root /var/spool/journeyman/signals-dev1

sudo /opt/journeyman/venv314/bin/python3 \
  /opt/journeyman/bin/journeyman-remote-runner register \
  --server https://journeyman.example \
  --token '<dev-runner-2-token>' \
  --config /etc/journeyman/remote-runner-dev2.env \
  --work-root /var/lib/journeyman/remote-jobs-dev2 \
  --signal-spool-root /var/spool/journeyman/signals-dev2
```

Create the four instance directories and give them to the `journeyman` account:

```bash
sudo install -d -o journeyman -g journeyman -m 0700 \
  /var/lib/journeyman/remote-jobs-dev1 \
  /var/lib/journeyman/remote-jobs-dev2
sudo install -d -o journeyman -g journeyman -m 2770 \
  /var/spool/journeyman/signals-dev1 \
  /var/spool/journeyman/signals-dev2
```

Install `deploy/systemd/journeyman-remote-runner@.service` as
`/etc/systemd/system/journeyman-remote-runner@.service`, then enable the two
instances:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now journeyman-remote-runner@dev1.service
sudo systemctl enable --now journeyman-remote-runner@dev2.service
```

With the built-in Package, the Runner name is also used as the systemd instance
identifier, so use only letters, numbers, `.`, `_` and `-` (for example
`dev-runner-1`). The Journeyman control plane still identifies each runner by
its independently registered runner UUID and Runner record name. Both Runner
records may report the same physical hostname.

Same-host development instances intentionally do not reconcile host-global SNMP
trap receiver units, because doing so could disrupt a sibling runner instance.
Use separate hosts for full Signal receiver validation.

## Signal receiver capabilities

Execution itself needs no per-runner listener port. Any capability which does
listen locally still needs unique resources. In particular, two SNMP Trap
Sources on runners sharing one host cannot bind the same UDP address/port at
the same time. Give those sources different ports, or test the listener
capability on only one same-host runner.

## Teardown

Stop and disable the instance before unregistering/deleting its Runner record:

```bash
sudo systemctl disable --now journeyman-remote-runner@dev1.service
sudo /opt/journeyman/venv314/bin/python3 \
  /opt/journeyman/bin/journeyman-remote-runner unregister \
  --config /etc/journeyman/remote-runner-dev1.env
```
