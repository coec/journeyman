# OWASP ASVS Coverage for Journeyman

Status: requirement assessment complete; automatable v1 closeout pass completed


## Automated v1 closeout checkpoint

The coordinated automated closeout pass raised deterministic automated coverage
from 116 to 124 ASVS requirements and reduced Deferred requirements from 54 to
46. The pass specifically closed parser-encoding consistency, bounded browser
sessions, nonce-based CSP, outbound HTTP message-size bounds, Active Directory
session revalidation, managed TLS cipher policy, release archive source-control
metadata exclusion, and managed Nginx directory-index suppression.

The remaining Deferred controls are intentionally left as release backlog where
they require architectural features, deployment/integration verification, or
external security tooling rather than a defensible in-process pytest assertion.

## Baseline

Journeyman uses **OWASP Application Security Verification Standard (ASVS)
5.0.0** as the pre-v1.0 application-security verification baseline.

ASVS requirement identifiers recorded here or in tests must include the version,
for example `v5.0.0-1.2.5`. This avoids ambiguity when requirement numbering or
wording changes in a later ASVS release.

This document is an evidence index, not a declaration of compliance. The final
v1.0 security review must determine the applicable ASVS verification level and
complete a requirement-by-requirement applicability assessment.

## Required status values

Every applicable ASVS requirement must end up in exactly one of these states:

| Status | Meaning |
| --- | --- |
| `Automated` | A deterministic automated test verifies the requirement. |
| `Pipeline Verified` | Automated verification exists and is required to pass in the release/CI pipeline. |
| `Manually Verified` | Verification requires documented human inspection or an operational/deployment check. |
| `Not Applicable` | The requirement does not apply to Journeyman; justification is recorded. |
| `Deferred` | The requirement is applicable but intentionally postponed; justification, risk and release impact are recorded. |
| `Unassessed` | Temporary working state only. Applicability/evidence has not yet been reviewed; this state is not permitted for a completed v1.0 assessment. |

`Deferred` is not equivalent to passing. Any Deferred item that is required for
the chosen v1.0 verification target blocks a claim that the target has been met.

## Evidence rules

A mapping entry should identify concrete evidence, such as:

- pytest test node(s), for example
  `tests/test_project_package_security.py::test_...`;
- a code/configuration location plus a manual verification procedure;
- an installation/deployment check;
- an independent review or penetration-test finding reference;
- a documented Not Applicable rationale.

A UI behaviour is not sufficient evidence of authorization. Authorization
coverage should exercise the server route/action directly with an identity that
must be denied.

Do not duplicate an existing functional test merely to create a security-named
test. Existing tests may be marked `security` and referenced from this matrix.

## Mapping record format

The requirement-level matrix should use this shape:

| ASVS requirement | Applicability | Status | Evidence | Notes / gap |
| --- | --- | --- | --- | --- |
| `v5.0.0-x.y.z` | Applicable | Automated | `tests/...::test_name` | Existing coverage. |
| `v5.0.0-x.y.z` | Applicable | Manually Verified | `docs/security/...` | Verify production proxy setting. |
| `v5.0.0-x.y.z` | N/A | Not Applicable | N/A rationale | Feature not present. |
| `v5.0.0-x.y.z` | Applicable | Deferred | issue/roadmap item | Blocks v1.0 until resolved. |

The authoritative Journeyman working matrix is
`docs/security/ASVS_MATRIX.csv`. It was generated from the official OWASP ASVS
5.0.0 English CSV release and contains all 345 requirement identifiers, their
chapter/section names and verification levels. The matrix deliberately does not
copy the full ASVS requirement text; use the official ASVS 5.0.0 release when
interpreting a requirement.

Source: OWASP Application Security Verification Standard 5.0.0, tag `v5.0.0`,
English CSV. ASVS is licensed by OWASP under CC BY-SA 4.0.

The initial matrix uses `Unassessed` for every row. This is intentional: no
control is credited merely because a framework or library is expected to provide
it. Each row must be reviewed against Journeyman code, tests and deployment
assumptions before its status changes.

Use the dependency-free helper to validate and summarize progress:

```bash
python scripts/asvs_matrix.py --check
```

The helper also checks that all 345 ASVS 5.0.0 rows are present, requirement IDs
are versioned, final verification states have evidence, and `Not Applicable` or
`Deferred` rows have a justification.

The helper also validates `ASVS_DEFERRED.csv`: every matrix row whose status is
`Deferred` must appear exactly once in the release backlog, no backlog row may
refer to a control that is no longer Deferred, dispositions must be recognized,
and every row must include remediation intent. The summary reports the current
release disposition counts.


## Deferred-control release triage

The requirement-by-requirement applicability assessment is complete: the matrix
must contain zero `Unassessed` rows before deferred work is triaged.

`docs/security/ASVS_DEFERRED.csv` is the release backlog for controls whose
matrix status remains `Deferred`. It uses three dispositions:

- `Pre-release required` — blocks public v1.0 unless implemented and verified,
  or explicitly accepted through a documented release-risk exception;
- `Pre-release desirable` — should be completed before v1.0 where practical,
  but may be accepted with documented mitigation and review;
- `Accepted post-release` — consciously retained backlog, generally Level-3,
  infrastructure-dependent, or disproportionate for v1.0. This remains an ASVS
  `Deferred` result, not a pass.

The backlog and matrix must be updated in the same change whenever a Deferred
control is implemented, reclassified, or newly introduced.

## Technology-specific applicability

The applicability decisions for ASVS V9 Self-contained Tokens, V10 OAuth and OIDC, and V17 WebRTC are documented in `TOKEN_OAUTH_WEBRTC_APPLICABILITY.md`. V9 is intentionally only partially Not Applicable because Flask browser sessions are signed self-contained cookies.

## Assessment workflow

Work through the matrix by chapter/section rather than editing statuses in bulk:

1. decide applicability for the requirement;
2. search existing pytest/code/deployment documentation for concrete evidence;
3. reuse existing tests where they genuinely verify the control;
4. add or strengthen tests where deterministic automation is practical;
5. document a manual verification procedure where automation is not suitable;
6. use `Not Applicable` only with a specific rationale;
7. use `Deferred` only with the remaining risk and release impact recorded;
8. run `python scripts/asvs_matrix.py --check` after matrix changes.

The first implementation pass should prioritise ASVS Level 1 controls and
Journeyman-specific high-risk boundaries (authorization, credentials/secrets,
Package inputs, repository/path/subprocess handling, inventory targeting and
runner authentication), then continue through the remaining applicable Level 2
and Level 3 requirements chosen for the v1.0 target.

## Initial Journeyman coverage inventory

The first mapping pass should reuse existing tests before adding new ones. The
following areas are known to have security-relevant pytest coverage in the
current codebase and should be mapped to the appropriate ASVS requirements:

### Authorization and object access

- Package launch permission matrix.
- Cross-user Job visibility restrictions.
- Credential ownership/reveal restrictions.
- Administrative access restrictions for system configuration and operational
  administration pages.
- Runner-management authorization.
- Direct route/action checks where already present.

Required gap analysis:

- Build a complete object/action permission matrix covering read, create, edit,
  delete, launch, rerun, cancel, schedule and reveal operations as applicable.
- For each protected action, verify owner/user/team/admin behaviour and direct
  crafted-request behaviour.
- Confirm API endpoints, if/when introduced, enforce the same policy as HTML
  routes.

### CSRF and session handling

- Existing CSRF tests should be mapped rather than recreated.

Required gap analysis:

- Verify all state-changing browser routes are covered.
- Review session cookie flags, expiry, login/logout and invalid-session handling
  against the chosen production deployment model.

### Package and input security

Existing Package permission and validation tests should be reused.

Required gap analysis:

- Validate choice, boolean, integer, email and other typed inputs against bypass
  via crafted requests.
- Verify fixed Package values cannot be overridden by a launcher.
- Verify hidden/conditional inputs cannot be injected contrary to Package rules.
- Verify inventory bindings use validated Package values and cannot introduce
  arbitrary template/code evaluation.

### Credentials and secret handling

Existing credential access-control and snapshot tests should be reused where
applicable.

Required gap analysis:

- Search rendered responses and Job output for known test secrets.
- Verify audit events and exception paths never record secret material.
- Review credential encryption format, key storage, rotation/recovery behaviour,
  and backup implications.
- Verify remote-runner dispatch contains only secrets required for that
  execution and that transport/runner identity meets the pre-v1.0 design.

### Repository and executable-file safety

Required mapping/tests:

- Repository revision used by a Job is immutable/snapshotted.
- Playbook/shell paths cannot escape the repository snapshot.
- Symlink/path traversal behaviour is explicitly tested.
- Shell Projects cannot turn user-supplied arbitrary command text into an
  executable step.
- Repository refresh/failure behaviour fails safely.

### Inventory and target integrity

Required mapping/tests:

- Filtered/composite inventory resolution cannot silently widen scope on invalid
  input.
- Missing Package inventory bindings fail closed.
- Package/Project preview and confirmed execution use the same approved
  inventory snapshot.
- External inventory freshness/refresh behaviour does not substitute a new host
  set after confirmation.
- Mid-workflow refresh only occurs where configured and preserves immutable
  execution constants.
- Protected inventory parameter values are not rendered/logged.
- Runner-routing inventory data cannot cause unsafe fallback.

### Runner security

Existing runner registration, dispatch and provenance tests should be mapped.

Required gap analysis:

- Registration tokens are single-use and expire appropriately.
- Heartbeat/dispatch authentication rejects invalid credentials.
- Disabled/offline/incompatible runners cannot receive work.
- Lost-runner behaviour fails the slice rather than replaying uncertain work.
- Exact runner provenance is retained in Job results.
- Complete the planned internal CA, certificate lifecycle and mTLS identity work
  before v1.0.

### Output encoding and browser security

Required mapping/tests:

- Stored/reflected strings from Job output, repository metadata, inventory data,
  audit data and descriptions are HTML-escaped.
- Review every use of raw/safe HTML rendering.
- Review JavaScript DOM insertion of server/external strings.
- Verify appropriate HTTP security headers in the supported production
  deployment.

### Error handling and logging

Required mapping/tests:

- Production errors do not expose stack traces, secret configuration or
  filesystem internals unnecessarily.
- Authorization failures do not leak protected object contents.
- Audit/log records contain sufficient security provenance without secrets.

### File, temporary-data and filesystem controls

Required mapping/tests/review:

- Temporary credential/inventory/execution files have restricted permissions and
  are removed when no longer required.
- Persistent paths under `/var/lib/journeyman` and `/etc/journeyman` use
  appropriate ownership/modes.
- Artifact/download paths cannot traverse storage roots.
- Backup archives are treated as secret-bearing material.

### Deployment and dependency hardening

Primarily manual/pipeline evidence:

- Flask production configuration.
- Reverse-proxy/TLS assumptions.
- systemd hardening and service-account permissions.
- Database permissions and PostgreSQL deployment validation.
- Python/package dependency review and vulnerability management process.
- File ownership/modes for configuration and encryption material.

## Security pytest marker

Security-relevant tests may be marked without changing their feature-oriented
location:

```python
import pytest


@pytest.mark.security
def test_cross_user_job_access_is_denied(...):
    ...
```

Register the marker in pytest configuration before broad adoption so pytest does
not emit unknown-marker warnings.

A future pipeline should run at least:

```bash
pytest
pytest -m security
```

The full suite remains authoritative; the security subset is a convenience and
release evidence, not a replacement for functional regression testing.

## Pre-v1.0 completion criteria

ASVS mapping is complete only when:

1. every ASVS 5.0.0 requirement has an applicability decision;
2. every applicable requirement has verification evidence or an explicit
   Deferred record;
3. required automated tests run in the release pipeline;
4. manual verification evidence is current for the release candidate;
5. unresolved Deferred items have been reviewed against the v1.0 release gate;
6. the threat model has been reviewed against the release architecture;
7. deployment hardening, secret-storage/cryptography review, independent review
   and penetration testing have been completed as required by `ROADMAP.md`.

## Evidence passes

Evidence-producing assessment passes are recorded separately from this process
reference. The first V1/V2 pass is documented in
[`ASVS_EVIDENCE_V1_V2.md`](ASVS_EVIDENCE_V1_V2.md) and maps automated hostile-input
regressions and explicit Not Applicable decisions into the control matrix.

## Authorization assessment

The ASVS V8 Authorization assessment is recorded in `AUTHORIZATION.md` and the matrix. V8 is intentionally assessed to zero `Unassessed` requirements. Controls which Journeyman does not currently satisfy at ASVS Level 3 are recorded as `Deferred`, not treated as framework-provided or silently marked compliant.

## Session Management assessment

The ASVS V7 Session Management assessment is recorded in `SESSION_MANAGEMENT.md`
and the matrix. V7 is assessed to zero `Unassessed` requirements. Journeyman's
signed Flask session model is explicitly documented, including its current
sliding inactivity timeout and the limitations of stateless cookie revocation.
Controls requiring server-side revocation, an absolute maximum lifetime, session
inventory/administration, or step-up authentication are recorded as `Deferred`
rather than assumed to be provided by Flask.

## Security Logging and Error Handling assessment

The ASVS V16 Security Logging and Error Handling assessment is recorded in
`LOGGING_ERROR_HANDLING.md` and the matrix. V16 is assessed to zero
`Unassessed` requirements. Structured audit metadata, sensitive-value redaction,
authentication events, authorization denials, and generic last-resort error
responses have automated evidence. Central log forwarding, tamper-evident audit
retention, universal log-injection encoding, and several broader failure-handling
controls remain explicitly `Deferred`.

## Supporting assessment documents

- `AUTHENTICATION.md`
- `AUTHORIZATION.md`
- `SESSION_MANAGEMENT.md`
- `CRYPTOGRAPHY.md`
- `DATA_PROTECTION.md`
- `LOGGING_ERROR_HANDLING.md`
- `CONFIGURATION.md`

## Configuration assessment

The ASVS V13 Configuration assessment is recorded in `CONFIGURATION.md` and the
matrix. V13 is assessed to zero `Unassessed` requirements. Production mode now
fails safe by default: packaged services require the Journeyman environment file,
the web service pins `ProductionConfig`, and the development entry point no
longer forces debug mode. Controls requiring
a full outbound allowlist, vault/HSM integration, formal secret-rotation policy,
source-control-metadata-free release artifacts, and strict web-tier extension
allowlisting remain explicitly `Deferred`.
