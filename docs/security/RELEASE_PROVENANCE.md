# Release provenance and SBOM

Each Journeyman release has an immutable application version and an exact,
release-tested Python dependency set. The maintainer generates release
provenance after the dependency lock has been reviewed and the test suites have
passed.

Generate the provenance for the PostgreSQL production dependency set with:

```text
python scripts/build_release_provenance.py generate
```

This creates three release artifacts under `dist/`:

- `journeyman-<version>.manifest.json` — Journeyman version, SHA-256 hashes and
  sizes for release source files, the dependency-lock SHA-256 digest, and the
  exact direct/transitive dependency inventory;
- `journeyman-<version>.MANIFEST.sha256` — conventional SHA-256 file manifest;
- `journeyman-<version>.sbom.spdx.json` — SPDX 2.3 JSON software bill of
  materials for Journeyman and the complete locked Python dependency closure.

The manifest excludes source-control metadata, virtual environments, Flask
runtime state, caches, logs, key material, previous build output, and generated
provenance files. Symlinks are not followed.

The manifest may also be retained with an installed release. An administrator
can verify that release source files and the dependency lock have not been
modified:

```text
python scripts/build_release_provenance.py verify   /path/to/journeyman-<version>.manifest.json
```

A successful verification reports `Journeyman release integrity: VERIFIED`.
Modified or missing release files, a changed dependency lock, or a mismatched
`VERSION` causes a non-zero exit status.

## Release signing

The release manifest is designed to be signed by the maintainer's release key.
Signing is intentionally performed outside the running Journeyman application
so the private release-signing key is never present on a production Journeyman
server. For example, an organization may produce a detached OpenPGP signature
for `journeyman-<version>.manifest.json` and distribute the maintainer public key
through its normal trusted software-distribution channel.

The manifest proves release contents only when the manifest itself is obtained
through a trusted channel or its detached signature is verified.

## Dependency vulnerability review

The exact locked dependency set used by the SBOM is audited by the maintainer
before release and at least weekly for every supported release. See
`docs/security/DEPENDENCY_VULNERABILITY_MANAGEMENT.md` for the required
`pip-audit` workflow, remediation policy, and maintenance-release process.

