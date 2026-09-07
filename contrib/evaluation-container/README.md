# Journeyman evaluation container

This directory provides a small, self-contained Journeyman installation for
evaluation and development.

It is **not** the supported production deployment model. Journeyman is designed
to run natively on Linux using systemd and Python virtual environments. If you
choose to build a production container deployment, container-specific support
is your responsibility.

The evaluation image currently uses Debian Bookworm with Python 3.14. This is
intentionally different from Journeyman's normal RHEL-oriented deployment and
also provides a useful portability test of the application itself.

## Build context

The container must be built with the **Journeyman repository root** as the build
context, not this directory.

This is required because the Dockerfile copies files such as
`requirements.lock`, the application source, migrations and Ansible content
from the repository root.

For the same reason, `.dockerignore` belongs in the repository root.

## Quick start with Podman

Podman does not require Compose and is a convenient way to test the evaluation
image on RHEL and other systems where Docker Compose or `podman-compose` is not
installed.

From the Journeyman repository root:

```bash
podman build \
  --no-cache \
  -t journeyman-evaluation:local \
  -f contrib/evaluation-container/Dockerfile \
  .
```

Create the persistent data volume:

```bash
podman volume create journeyman-evaluation-data
```

Run Journeyman:

```bash
podman run \
  --name journeyman-evaluation \
  --replace \
  -p 8080:5000 \
  -v journeyman-evaluation-data:/var/lib/journeyman \
  -e JOURNEYMAN_EVALUATION=1 \
  journeyman-evaluation:local
```

Leave the first run in the foreground so the initial startup output and
fallback administrator password are visible.

Open:

```text
http://localhost:8080/
```

If accessing the container from another machine, replace `localhost` with the
hostname or IP address of the container host.

Useful Podman commands:

```bash
podman ps
podman logs -f journeyman-evaluation
podman exec -it journeyman-evaluation bash
```

To stop and remove the container while keeping its persistent Journeyman data:

```bash
podman rm -f journeyman-evaluation
```

To completely reset the evaluation instance:

```bash
podman rm -f journeyman-evaluation 2>/dev/null || true
podman volume rm journeyman-evaluation-data
podman volume create journeyman-evaluation-data
```

## Quick start with Docker Compose

From this directory:

```bash
docker compose up --build
```

The supplied `compose.yaml` uses the Journeyman repository root as its build
context.

Open:

```text
http://localhost:8080/
```
Docker Compose requires a Compose provider. On systems where the `docker`
command is provided by Podman compatibility packages, `docker compose` will
still fail unless Docker Compose or `podman-compose` is installed. Use the plain
Podman instructions above if no Compose provider is available.

## First startup

On first startup, the container logs contain:

```text
Fallback administrator username: admin
Fallback administrator password (shown once): ...
```

With Docker Compose, find it with:

```bash
docker compose logs journeyman | grep -A2 "Fallback administrator username"
```

With Podman:

```bash
podman logs journeyman-evaluation \
  | grep -A2 "Fallback administrator username"
```

The evaluation activation has no automatic expiry. This is deliberate for a
throw-away demonstration environment and is strongly discouraged for normal
deployments.

Signing out still ends the activation.

To create a fresh activation with Docker Compose:

```bash
docker compose exec journeyman \
  flask --app run.py fallback-admin generate --no-expiry
```

With Podman:

```bash
podman exec -it journeyman-evaluation \
  /opt/journeyman/venv/bin/flask \
  --app /opt/journeyman/run.py \
  fallback-admin generate --no-expiry
```

## Included examples

A disk repository named `Journeyman examples` is created automatically. Its
files are stored on the persistent Docker volume at:

```text
/var/lib/journeyman/repositories/demo-repository
```

It contains:

```text
playbooks/
  hello.yml
  ping.yml
  facts.yml
  uptime.yml
scripts/
  hello.sh
  show-environment.sh
README.md
```

The sample inventory is intentionally not inserted directly into Journeyman's
database. Add a **Static** inventory through the normal UI with the content:

```yaml
all:
  hosts:
    localhost:
      ansible_connection: local
```

The sample inventory uses `ansible_connection: local`, so Journeyman does not
need to SSH to another machine. A machine credential should still be assigned
to the sample Projects. This satisfies Journeyman's normal Project credential
requirements and also provides privilege-escalation credentials if a playbook
requires `become`.

## Credentials

`svc_journeyman` has been pre-configured in the container but needs to be added
to Journeyman. Add `svc_journeyman` as a `machine` credential and select it on
the sample Projects. Although localhost execution does not require SSH
authentication, Journeyman Projects normally require a machine credential, and
one may also be required for privilege escalation.

Do not provide a password; instead use `svc_journeyman`'s private key. Obtain
the private key using:

```bash
podman exec journeyman-evaluation \
  cat /home/svc_journeyman/.ssh/id_ed25519
```

## Persistence

The evaluation container stores `/var/lib/journeyman` in a named container
volume. This includes the SQLite database, generated secrets, Jobs, runner data
and the editable sample repository.

Normal container filesystem changes outside that volume are disposable.

With Docker Compose, remove the evaluation instance and its persistent volume
with:

```bash
docker compose down -v
```

With Podman:

```bash
podman rm -f journeyman-evaluation 2>/dev/null || true
podman volume rm journeyman-evaluation-data
```

## Container internals

The evaluation container does not run systemd. Its entrypoint starts the
Journeyman components directly:

```text
database migrations
evaluation repository initialization
fallback administrator initialization
Journeyman scheduler
Journeyman local runner
Journeyman environment builder
Gunicorn web application
```

The application virtual environment remains at Journeyman's normal path:

```text
/opt/journeyman/venv
```

Required runtime directories under `/var/lib/journeyman` are created by the
entrypoint because the persistent volume hides any directories created there
during the image build.

## Published image

Release tags can publish:

```text
ghcr.io/coec/journeyman-evaluation:<tag>
```

The workflow is `.github/workflows/evaluation-container.yml`.

Once an image has been published, replace the `build:` section in a local
Compose file with:

```yaml
image: ghcr.io/coec/journeyman-evaluation:latest
```

The published image is intended for demonstration, evaluation and development.
Running Journeyman in containers for production use remains entirely at the
operator's discretion and container-specific deployment, persistence,
networking, upgrade and troubleshooting issues are operator-supported unless
explicitly documented otherwise by the Journeyman project.
