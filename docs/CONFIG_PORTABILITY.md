# Journeyman configuration portability

`./scripts/journeyman-config` exports and imports portable Journeyman
configuration using YAML.

## Export

```bash
./scripts/journeyman-config --export journeyman.yaml
./scripts/journeyman-config --export journeyman-enabled.yaml --enabled-only
```

`--enabled-only` exports enabled Projects and Packages plus the repositories,
inventories, environments, runner crews, inventory parents, and other
definitions required to reconstruct those enabled objects.

### Export one Package

```bash
./scripts/journeyman-config \
  --export cisco-port-control.jxf \
  --package "Cisco Port Control"
```

`--package` exports only the named Package and its complete dependency closure:
its Project and steps, repositories, inventories (including filtered/composite
parents), environments, and runner-crew references. Credential values are
never included; the command prints the credential names, owners, and types
that must exist on the destination.

`--package` may be repeated. Shared Projects and dependencies are deduplicated:

```bash
./scripts/journeyman-config \
  --export network-tools.jxf \
  --package "Shutdown Cisco Port" \
  --package "Bounce Cisco Port"
```

`--package` and `--enabled-only` are mutually exclusive.

For safety, Package-focused exports omit Project schedules. Importing a
community/shared Package must never create scheduled execution merely because
the source Project happened to have a schedule.

The `.jxf` extension is recommended for Package/community exchange files. JXF
remains human-readable YAML.

The export file is written mode 0600 where the operating system permits.

## Import and dry-run

```bash
./scripts/journeyman-config --import journeyman.yaml --dry-run
./scripts/journeyman-config --import journeyman.yaml

By default, import refuses to overwrite an existing Repository, Environment,
Inventory, Runner Crew, Project, or Package with the same name. This prevents a
portable JXF from silently changing live destination configuration. If an
administrator has reviewed the collision and intentionally wants replacement,
use:

```bash
./scripts/journeyman-config --import journeyman.yaml --replace-existing
```

Existing static inventories and built-in environments are destination-owned
dependencies: they may be reused by name without being modified.
```

Import is idempotent by stable names, not database IDs. Existing named objects
are updated and missing objects are created. Project steps, schedules, Package
inputs, and Package permissions are reconstructed from the export.

Run `--dry-run` first when importing into an existing instance.

## Credential safety

Credential records are **not exported**. JXF uses anonymous logical
credential references such as `credential_1`. The requirement table contains
only the credential display name and type; it never contains the source
credential owner or username. It never contains:

- encrypted credential data or ciphertext;
- credential encryption key IDs;
- passwords, tokens, SSH private keys, or become passwords;
- runner registration tokens, runner API secrets, or their digests;
- environment-build proxy passwords;
- application/session/CSRF keys;
- browser sessions, Job credential snapshots, inventory snapshots, caches,
  logs, or audit history.

Before import, Journeyman checks every credential reference. There must be
exactly one local Credential with the required name and type. Zero matches is
unresolved; multiple matches is ambiguous. Either condition fails preflight
before configuration changes are made. Credential ownership remains entirely
local to the destination.

Runner registrations are handled similarly: runner secrets are never portable.
A referenced Runner must already be registered on the destination.

## Trust and identity are never portable

A JXF installs automation definitions. It does not grant trust.

The exporter does not emit Project or Package owners, Package permissions,
AD users/groups, GUIDs/DNs, security scopes, built-in keys, Package access
mode, or Project/Package enabled state.

Import does not merely ignore those fields. It **rejects the JXF** if they are
present. This prevents a hand-edited or malicious JXF from requesting identity,
authority, or trust that the exporter itself would never produce.

Imported state is assigned locally:

- Projects: disabled, owner `system`, security scope `private`;
- Packages: disabled, owner `system`, access mode `restricted`, zero
  permissions;
- imported Project schedules: disabled and locally attributed to
  `config-import`.

An administrator must review and explicitly enable/share imported automation.

## Static inventories

Static inventory content is deliberately omitted. Static inventory YAML may
contain arbitrary host variables, including passwords and private keys, so it
cannot be proven safe by a generic exporter.

The export contains the static Inventory definition but marks its content as
not exported. Existing destination static content is preserved. A new static
inventory imported from an export is created empty and reported as a warning;
populate it manually before use.

Satellite, Zabbix, filtered, and composite inventory configuration is exported
through explicit allowlisted serializers. Filtered/composite inventory
references use inventory names rather than database IDs.

## Security model

This is not a generic ORM/database serializer. Every exported object has an
explicit allowlist of fields in `app.services.config_portability`. Adding a new
database column does not automatically make that field exportable.

Package secret inputs never export a default value, even if legacy data
contains one.

The top-level metadata contains:

```yaml
journeyman_export:
  format_version: 1
  journeyman_version: ...
  exported_at: ...
  enabled_only: false
  selected_packages: []
  package_exchange: false
  contains_secret_material: false
```

Import rejects unsupported format versions and any document marked as
containing secret material.
