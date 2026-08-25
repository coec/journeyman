# Versioning Policy

Journeyman versions use the form `x.y.z`:

- `x` — major version
- `y` — minor version
- `z` — maintenance/release version

## Major version

Incremented for incompatible changes to established Journeyman
interfaces, automation contracts, deployment architecture, APIs,
JXF formats, or other supported behaviours that require users or
integrations to change.

The minor version component is reset to zero.

## Minor version

Incremented when backward-compatible functionality is added.

Examples include new inventory/source types, API capabilities,
Ansible modules, workflow features, administrative functions, or
other user-visible capabilities.

The maintenance component is reset to zero.

## Maintenance/release version

Incremented for changes that preserve existing functionality and
interfaces, including:

- bug fixes
- security fixes
- dependency updates
- vulnerability remediation
- performance fixes
- UI corrections
- packaging/install fixes
- documentation or release-metadata corrections

A change to the locked production dependency set requires a new
maintenance release even when Journeyman source code is otherwise
unchanged.

## Compatibility

Beginning with Journeyman 1.0.0, published interfaces and release
artifacts are treated as supported compatibility contracts.

A published Journeyman version is immutable. Its source, dependency
lock, manifest, and SBOM must not be silently replaced. Any change
requires a new version.
