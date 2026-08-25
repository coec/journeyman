# Journeyman Threat Model

Status: initial pre-v1.0 threat model

This document records Journeyman's principal assets, trust boundaries, threat
actors, abuse cases, and expected controls. It is intended to change with the
architecture. Any feature that creates a new trust boundary, execution path,
credential path, or externally reachable interface must update this threat
model as part of the change.

## Security objectives

Journeyman must preserve:

- **Confidentiality** of credentials, runner secrets, authentication/session
  material, sensitive inventory data, repository contents where access is
  restricted, and protected Job data.
- **Integrity** of Projects, Packages, repositories, inventory definitions,
  runtime inputs, execution snapshots, runner routing, schedules, and audit
  history.
- **Authorization boundaries** between administrators, ordinary users, object
  owners, teams, Package launchers, and users viewing historical Jobs.
- **Execution integrity** so that the systems, code, variables, credentials,
  inventory and runner used by a Job correspond to the approved/previewed
  execution.
- **Availability** sufficient to avoid a security control being bypassed when a
  dependency is unavailable. Availability must not be achieved by unsafe
  fallback behaviour.
- **Accountability** through useful audit and immutable execution provenance,
  without leaking secrets into logs.

## Important assets

### Secrets and identity

- Stored credentials and their encryption key material.
- Immutable Job credential snapshots.
- User authentication/session state.
- LDAP/directory identities and team membership.
- CSRF tokens.
- Remote-runner API secrets and registration/bootstrap tokens.
- Future runner certificate/private-key material.

### Automation and execution state

- Projects and Project steps.
- Packages, fixed values, prompted inputs, conditions and launch permissions.
- Repositories, configured Git locations and immutable repository snapshots.
- Playbooks and repository-backed scripts.
- Execution environments and associated configuration.
- Schedules.
- Job, step and execution-slice records.
- Runner selection and runner provenance.

### Inventory and target selection

- Inventory definitions and external inventory credentials.
- Cached and immutable inventory snapshots.
- Filtered/composite inventory rules.
- Package-provided inventory bindings.
- Host variables and protected external-source parameters.
- Per-host runner-routing metadata.

### Operational evidence

- Job output and host results.
- Audit events.
- Repository/inventory refresh status.
- Runner heartbeat and health state.
- Backup archives, which may contain the database, encryption material and
  managed environments.

## Trust boundaries

### Browser -> Journeyman web application

The browser is untrusted. Requests may be modified, replayed, sent directly to
routes that are not linked in the UI, or contain unexpected form/JSON values.
Authentication, authorization, CSRF protection, validation and output encoding
must be enforced on the server.

### Journeyman -> database

The database contains security-critical configuration and execution history.
Application code must not trust database values merely because Journeyman wrote
them previously: migrations, administrative changes, restored backups and
legacy data may violate newer assumptions.

### Journeyman -> Git repositories

Repository content is executable input. A repository or branch may be changed
by someone outside Journeyman. Execution must use the intended immutable
revision/snapshot, and paths supplied by repository content or configuration
must not escape allowed roots.

### Journeyman -> inventory providers

Satellite/Foreman, Zabbix and other external inventory systems are trusted only
for the data that the configured integration is intended to consume. Their
responses may be stale, malformed, unexpectedly large, or contain strings that
become HTML, Ansible variables, hostnames, file content, or runner-routing
metadata.

### Journeyman -> local execution

Local runner subprocesses cross from the web/control application into code that
can execute operating-system commands and automation. Arguments, environment
variables, temporary files, repository paths and credentials must be constructed
without command-injection or path-traversal opportunities.

### Journeyman -> remote runners

A remote runner is a separate trust domain with network reachability and
execution privileges that may differ from the Journeyman server. Journeyman
must authenticate runners, authenticate dispatch, reject stale/invalid runner
identity, and preserve which runner actually executed each slice. Remote
runners must not trust work from unauthenticated parties.

### Runner -> managed hosts

Ansible, shell and future execution types may make privileged changes to managed
systems. Journeyman's security model must assume that incorrect target
selection, credentials, variables or scripts can have production impact.
Preview/snapshot integrity is therefore security-relevant.

### Backup/restore boundary

Backup archives contain enough material to reconstruct a Journeyman instance
and may include secrets. A backup must be treated as highly sensitive data.
Restore must not accept incompatible or corrupted input without validation.

## Threat actors

The threat model includes:

- An authenticated ordinary user attempting to exceed granted permissions.
- An authenticated user attempting to access another user's protected objects or
  Jobs.
- A malicious or compromised administrator. Some administrator actions are
  intentionally powerful; auditability and secret-handling still matter.
- A user permitted to modify a Git repository but not administer Journeyman.
- A compromised external inventory source.
- A compromised or impersonated runner.
- An unauthenticated network client able to reach the web application or runner
  endpoints.
- Accidental administrator/operator mistakes that have the same effect as an
  attack, such as targeting an unexpectedly broad inventory.

## Primary abuse cases and required controls

### Broken object-level authorization / IDOR

An attacker changes an object ID or calls a hidden route directly to view,
modify, delete, launch, cancel or rerun an object they do not control.

Required controls:

- Central server-side ownership/scope/team/admin checks.
- Denial tests for direct GET and state-changing requests.
- Consistent checks across HTML routes and any management API.
- No reliance on navigation visibility as an access control.

### Privilege escalation through crafted form data

An ordinary user supplies fields normally rendered only for administrators,
changes ownership/security scope, selects an unauthorized credential, or posts
values not available in the UI.

Required controls:

- Ignore/reject unauthorized fields server-side.
- Re-resolve referenced objects and permissions on submission.
- Test direct crafted requests, not only rendered forms.

### Credential or secret disclosure

Secrets leak through reveal routes, Job output, exception traces, logs, audit
records, Package previews, inventory host variables, backups or runner payloads.

Required controls:

- Explicit reveal authorization where reveal is supported.
- Redaction rules at security boundaries.
- Never render protected inventory parameter values merely for diagnostics.
- Regression tests search responses/logical snapshots for known test secrets.
- Production error handling must not expose stack traces or configuration.

### Package/input injection

A Package input is used to inject shell, Ansible, path, inventory-template or
other executable syntax beyond what the Package author intended.

#### Injection and "Bobby Tables"

The classic xkcd #327, *Exploits of a Mom* ("Bobby Tables"), demonstrates what
happens when untrusted data is concatenated into executable SQL:
https://xkcd.com/327/

Journeyman must not attempt to prevent this class of vulnerability merely by
removing punctuation from arbitrary text. Untrusted values must remain data at
each execution boundary. Database operations use SQLAlchemy parameterisation
rather than SQL assembled by string concatenation; normal Jinja rendering must
retain output escaping; YAML must be parsed safely and then validated; and
subprocess arguments must be passed structurally without shell interpretation
unless shell execution is explicitly the feature being invoked.

Fields with narrower semantics must additionally validate those semantics. For
example, an Ansible configuration path is not arbitrary prose. Journeyman
requires it to be an absolute POSIX-style `.cfg` path using a conservative
pathname character set and rejects parent-traversal components. A manual test
using the value:

```text
/etc/ansible/ansible.cfg; touch /tmp/I_GOT_HACKED
```

did not execute `touch`, but the value was initially accepted as the configured
path. That demonstrated that the subprocess boundary was not interpreting the
semicolon as shell syntax, while also exposing insufficient field validation. A
regression test now requires this exact payload to be rejected.

Inventory binding expressions such as `{{ clustername }}` are substitution
tokens, not a general Jinja execution environment. Only the restricted binding
syntax may be interpreted; arbitrary Jinja expressions or statements supplied
through user or inventory data must not be evaluated.

Required controls:

- Typed validation and explicit choices where appropriate.
- Semantic allowlists for constrained fields such as identifiers and paths.
- SQLAlchemy parameterisation; no SQL assembled from untrusted strings.
- Preserve Jinja output escaping for untrusted values.
- Safe YAML parsing followed by type/schema validation.
- Structured subprocess argument passing and `shell=False` unless shell
  execution is explicitly required by the execution type.
- Package conditions/templates use a constrained documented language.
- Inventory bindings substitute declared values only; they do not evaluate
  arbitrary Python or user-provided template expressions.
- Shell Projects execute repository-backed regular files rather than arbitrary
  command text supplied by users.
- Regression tests cover SQL-like payloads, HTML/script payloads, template
  expressions, path traversal and shell metacharacters at relevant sinks.

### Repository/path traversal and executable-content substitution

An attacker causes Journeyman to execute a file outside the immutable repository
snapshot, substitutes a different revision between preview and execution, or
uses symlinks/path traversal to access unintended files.

Required controls:

- Canonical path containment checks.
- Regular-file checks where executable files are expected.
- Immutable repository revision/snapshot recorded in the Job.
- No post-preview branch-head re-resolution for confirmed execution.

### Inventory widening or target substitution

A filter, binding, external inventory change, limit, or dependency causes a Job
to target hosts other than those approved by the launcher.

Required controls:

- Preview effective hosts before execution where applicable.
- Refresh external sources before initial preview when freshness is required.
- Queue/execute the inventory snapshot that was previewed; do not silently
  refresh again on confirmation.
- Snapshot immutable Package inventory bindings.
- Mid-workflow refresh occurs only when explicitly configured.
- Large/unexpected target sets should be visible and confirmable.
- Missing bindings or invalid filters fail closed rather than widening scope.

### Runner impersonation or routing manipulation

An attacker registers a fake runner, steals a runner secret, changes host routing
metadata, or tricks Journeyman into executing work at the wrong site.

Required controls:

- Strong registration/bootstrap secret handling.
- Authenticated heartbeat and dispatch.
- Explicit runner identity and immutable Job/slice provenance.
- No silent fallback to local or another runner when routing cannot be
  satisfied.
- Future pre-v1.0 work: internal CA, certificate lifecycle and mTLS runner
  identity.

### CSRF/session abuse

An attacker causes an authenticated browser to perform a state-changing action,
or steals/reuses session material.

Required controls:

- CSRF protection on state-changing browser requests.
- Secure session-cookie configuration appropriate to the deployment.
- Session invalidation/expiry behaviour reviewed and tested.
- Authentication state must be established by Journeyman's configured identity
  mechanism, not client-supplied usernames.

### Stored/reflected XSS

Repository names, inventory values, hostnames, Job output, descriptions, audit
fields or other externally sourced strings are rendered as executable browser
content.

Required controls:

- Default template escaping remains enabled.
- Any explicit `safe` rendering or raw HTML path requires security review.
- Dynamic JavaScript insertion uses text-safe APIs or appropriate encoding.
- Regression tests cover high-risk rendered external strings.

### Unsafe retry/failover

A runner disappears after an operation may have changed a host and Journeyman
replays the same work elsewhere, causing duplicate or destructive changes.

Required controls:

- No automatic runner fallback for already-started work.
- Lost runner fails its execution slice.
- No retry in v1 unless execution can be proven not to have started.
- Future retry semantics require explicit safety design.

### Denial of service / resource exhaustion

A user or external source causes excessive Job concurrency, enormous inventory
resolution, unbounded output, repository growth, or runner saturation.

Required controls:

- Existing Project/step concurrency limits are security-relevant.
- Storage usage under `/var/lib/journeyman` must be monitored.
- Large inventory/preview behaviour must remain bounded and reviewable.
- Runner dispatch should account for availability/capacity rather than creating
  unbounded local work.
- Additional quotas/limits should be added if production proving demonstrates a
  practical abuse path.

## Explicit non-goals / assumptions for v1

- Journeyman does not attempt to make malicious automation safe. A user who is
  legitimately permitted to modify and execute privileged repository automation
  can cause the effects of that automation.
- Journeyman does not provide transactional rollback of arbitrary Ansible or
  shell changes.
- Journeyman does not assume Jobs are idempotent.
- Runner redundancy does not imply transparent replay of work after a runner is
  lost mid-execution.
- Operating-system, reverse-proxy, database, LDAP, Git-server, Satellite,
  Zabbix, network and managed-host security remain shared deployment
  responsibilities and must be included in deployment hardening.

## Review triggers

Revisit this threat model whenever Journeyman adds or materially changes:

- authentication or authorization;
- credentials or encryption;
- an execution type;
- a runner transport or runner identity mechanism;
- a management API or event/webhook endpoint;
- repository acquisition/execution;
- inventory providers, filtering, bindings or refresh semantics;
- Package expression/input capabilities;
- file upload/download or artifacts;
- backup/restore;
- externally accessible integrations.
