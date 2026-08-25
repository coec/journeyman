# Journeyman Security

Journeyman is an automation controller. A successful compromise may provide an
attacker with access to credentials, inventories, automation repositories,
execution runners, and the systems managed by those runners. Security changes
must therefore be treated as correctness requirements, not optional hardening.

Journeyman is currently pre-1.0 software. The v1.0 release is gated on the
security-readiness work described in `ROADMAP.md` and `docs/security/`.

## Security baseline

Journeyman's application-security verification baseline is OWASP Application
Security Verification Standard (ASVS) 5.0.0. Requirement references in project
documentation should include the ASVS version, for example
`v5.0.0-1.2.5`, so that future ASVS releases do not silently change the meaning
of an existing mapping.

The project does not claim ASVS conformance merely because a requirement is
listed in the coverage matrix. A requirement is considered covered only when
there is recorded verification evidence.

See:

- `docs/security/THREAT_MODEL.md`
- `docs/security/ASVS.md`
- `docs/security/CRYPTOGRAPHY.md`
- `docs/security/DATA_PROTECTION.md`
- `docs/security/LOGGING_ERROR_HANDLING.md`
- `docs/security/CONFIGURATION.md`
- `docs/security/SECURE_CODING_ARCHITECTURE.md`
- `docs/security/WEB_FRONTEND_SECURITY.md`
- `docs/security/API_WEB_SERVICE.md`

## Security principles

Security-sensitive Journeyman code should follow these rules:

- Authorization is enforced server-side on every protected read and mutation.
  Hiding a button or navigation item is not an authorization control.
- The least privilege needed for an operation is preferred. Ordinary users must
  not gain administrative behaviour through direct URLs, crafted form data, or
  API requests.
- Secrets must not be exposed in HTML responses, Job output, logs, audit events,
  exception messages, inventory previews, or other user-visible diagnostics.
- Credentials and immutable credential snapshots remain encrypted at rest.
- One-time bootstrap or registration secrets must be single-use and short-lived.
- Job execution records are immutable evidence of what was requested and what
  was executed. Runtime inputs, repository revisions, inventory snapshots,
  runner provenance, and credential references must not silently change after a
  Job is queued.
- Untrusted strings are never treated as executable code merely because they
  originate from a Package, inventory, repository, Job output, or external
  integration.
- File access is constrained to intended roots. Repository paths, playbooks,
  shell scripts, artifacts, inventory files, and temporary files must not allow
  path traversal or arbitrary filesystem access.
- Failure must be safe. Journeyman must not silently widen a target inventory,
  fall back to a different runner/site, replay uncertain work, or continue with
  stale/invalid security-sensitive state merely to complete a Job.
- Security-relevant actions should be auditable without recording secret values.

## Security regression tests

Security tests are part of the normal pytest suite. Tests remain organised by
feature when that is the clearest location; security relevance does not require
moving a test into a separate directory.

Security-specific or cross-cutting tests may live under `tests/security/`.
Security-relevant tests should gradually be marked with:

```python
@pytest.mark.security
```

This allows both the complete test suite and a focused security regression run:

```bash
pytest
pytest -m security
```

The ASVS coverage matrix may point to tests anywhere in the repository.
Existing tests should be reused rather than duplicated solely to satisfy the
matrix.

## Reporting security issues

Do not include live credentials, encryption keys, runner secrets, session data,
production inventory contents, or other sensitive operational data in a public
issue.

Until a private vulnerability-reporting channel is published, security issues
should be reported privately to the project maintainer rather than opened as a
public issue. A public disclosure process should be established before the v1.0
release.

### Browser session signing secret

Production deployments must set a unique high-entropy `JOURNEYMAN_SECRET_KEY`.
Journeyman refuses to start in non-debug mode when the development fallback
secret is still configured or when the signing secret is empty. Reusing the same
signing secret across unrelated deployments increases the impact of key
compromise and should be avoided.
