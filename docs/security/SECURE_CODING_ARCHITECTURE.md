# Secure Coding and Architecture

This document records the security decisions used to assess OWASP ASVS 5.0.0
V15 for Journeyman.  It complements the threat model and the topic-specific
security documents in this directory.

## Third-party component policy

Journeyman treats direct and transitive Python dependencies as part of the
application attack surface.  Direct dependencies are declared in
`requirements.txt` and `requirements-postgresql.txt` and must be obtained from
administrator-approved Python package repositories.

Security fixes for a dependency should be prioritised by exploitability and
exposure in Journeyman:

* critical or actively exploited vulnerabilities affecting reachable
  Journeyman functionality: remediate or disable the affected functionality as
  soon as practical, with a target of 7 days;
* high-severity vulnerabilities affecting reachable functionality: target 30
  days;
* medium severity: target 90 days;
* low severity and routine library refreshes: review during normal maintenance.

These are remediation targets rather than a claim that a dependency scanner or
release gate currently enforces them.  A complete transitive SBOM and automated
vulnerability-policy gate remain required work before public release.

### Risky components

Components deserve additional review when they process complex untrusted data,
implement security primitives, or bridge Journeyman to another trust domain.
Current examples include:

* `cryptography`, because it protects stored credentials and remote execution
  envelopes;
* `ldap3`, because it handles directory authentication and directory-derived
  identity data;
* SQLAlchemy/database drivers, because authorization and immutable Job state
  depend on transactional persistence;
* Git and Ansible executables invoked by Journeyman, because they process
  repository or automation content outside the Flask process;
* Python archive, YAML, URL and TLS libraries where data can originate from a
  repository, runner, inventory provider, or administrator-managed endpoint.

## Resource-demanding functionality

The following operations can consume substantial CPU, memory, storage, network
bandwidth, process slots, or remote-system capacity:

* Project and Package execution;
* Ansible and shell execution on local or remote runners;
* repository synchronization and artifact creation;
* Satellite/Zabbix and other inventory refreshes;
* execution-environment builds;
* scheduled execution and mid-workflow inventory refresh.

Journeyman uses queued Jobs, per-Project parallel-step limits, runner
`max_concurrent_steps`, conditional database claims, and explicit subprocess and
provider timeouts to bound these operations.  These controls are intended to
prevent a single request from synchronously consuming an unbounded amount of
execution capacity.  They are not a substitute for deployment-level capacity
planning or denial-of-service protection.

## Dangerous functionality

Some Journeyman functionality is intentionally powerful and must not be treated
as ordinary string-processing code:

* shell Project steps intentionally execute shell programs;
* Ansible steps execute repository-controlled automation with configured
  credentials;
* Git synchronization obtains executable automation content;
* runner bootstrap and dispatch create remote execution capability;
* credential reveal exposes plaintext secrets to an explicitly authorized
  owner;
* inventory refresh connects to administrator-configured external systems;
* archive extraction materializes runner artifacts on disk;
* system-setting helpers can update deployment configuration.

Controls around these areas include administrator-only configuration, object
and field-level authorization, immutable Job snapshots, restricted subprocess
argument handling, path containment, safe archive extraction, encrypted secret
storage, runner authentication, TLS verification, audit logging, and explicit
execution concurrency limits.  Additional sandboxing or isolation beyond the
operating-system/service boundary is not currently claimed.

## Dependency inventory and build provenance

The checked-in requirements files are the source inventory for direct Python
requirements, but Journeyman does not yet maintain a release-specific,
transitive SBOM with hashes and provenance for every dependency.  Production
release work must add a repeatable SBOM generation process and dependency
vulnerability policy gate.  Dependency sources should be restricted by the
administrator/deployment tooling to trusted repositories; Journeyman currently
does not itself enforce repository provenance for every transitive Python
package.

## Defensive coding conventions

Journeyman security-sensitive handlers should:

* copy only explicitly allowed request fields into model objects rather than
  mass-assigning request dictionaries;
* return purpose-built response structures rather than serializing whole ORM
  objects;
* keep untrusted data separate from SQL, shell, template, and filesystem
  syntax;
* constrain proxy trust using the configured number of trusted proxies and use
  `request.remote_addr` only after that middleware boundary;
* use conditional database updates/transactions when claiming queued work;
* validate types and ranges at the trusted server layer for security-relevant
  values.

Python's dynamic type system means V15's strict type-safety requirement cannot
be treated as universally proven by the framework.  Likewise, HTTP parameter
pollution and all concurrency/TOCTOU cases require continued review as new
features are added.

## Concurrency model

Journeyman uses database state as the coordination point for Jobs and runner
claims.  Remote Job claiming uses a conditional update that succeeds only while
the Job is still queued and unassigned.  Project parallelism is capped, and
runner capacity is explicitly bounded.

Journeyman does not claim a general proof that every filesystem or database
check/action pair is atomic, nor does it implement a custom fair thread
scheduler.  Those Level-3 ASVS concurrency controls remain deferred unless a
specific protected resource is demonstrated to require and implement them.
