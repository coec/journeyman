# Data Protection

Journeyman handles data that can provide administrative access to managed OT/IT
systems. This document classifies that data and records the minimum protection
requirements used by the ASVS 5.0.0 assessment.

## Protection classes

### Restricted secrets

Examples include credential passwords/tokens, directory bind passwords, Package
fields marked secret, runner registration/API secrets, dispatch tokens, the
credential encryption key, and the Flask session-signing secret.

Required controls:

- never intentionally log or include the value in audit records;
- do not place the value in URLs or query strings;
- encrypt stored application payloads where Journeyman must retain the value;
- store non-recoverable high-entropy authentication secrets as digests where the
  original value is not required;
- reveal recoverable credential secrets only through an explicitly authorized
  action and return that response with `Cache-Control: no-store`;
- send runner API/dispatch secrets in request headers rather than URLs;
- use restrictive filesystem permissions for host-managed key/hash files;
- minimize plaintext lifetime to the execution/reveal operation that needs it.

### Protected operational data

Examples include inventory host variables/parameters, inventory snapshots, Job
inputs that are not explicitly public, Job stdout/stderr, repository revisions,
runner topology and audit records. Some inventory values may themselves contain
certificates, SSH material or other sensitive data and must therefore be treated
as protected even when the source system does not label them secret.

Required controls include authenticated/authorized access, no accidental logging
of protected source values, and no shared-browser/proxy caching where an endpoint
returns security-sensitive detail.

### Ordinary metadata

Names, descriptions and other values intended to be displayed broadly inside the
application remain subject to authorization and output encoding, but do not by
themselves require secret storage.

## Retention and known gaps

Immutable Job records and snapshots are intentionally retained as execution
evidence. Journeyman does not yet implement a configurable automatic retention
and secure-purge policy for all protected Job/inventory data; this remains a
pre-release/deployment-policy item.

Journeyman also does not currently apply `Cache-Control: no-store` to every
authenticated HTML response. Sensitive reveal, Job output, audit and remote
execution-data endpoints have explicit anti-caching controls, but complete
browser/proxy cache policy is still to be hardened.

Flask's signed client-side session cookie is integrity-protected but not encrypted.
It may contain authenticated identity/session metadata, so the ASVS requirement
against storing sensitive data in client storage is not yet claimed as satisfied.
This should be revisited when the session architecture is hardened further.

Journeyman currently has no general user file-upload feature, so submitted-file
metadata stripping is outside the current application surface.


## Retention and browser-cache hardening

The pre-release retention/cache gaps are addressed by the policy and controls in
`DATA_RETENTION.md`: completed Job evidence has configurable automatic retention,
source inventory caches have a shorter bounded lifetime, and authenticated
responses are centrally emitted with `Cache-Control: no-store`. Deployment-level
backup/snapshot expiration remains an infrastructure responsibility.
