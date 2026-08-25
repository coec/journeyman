# Security Logging and Fail-Closed Review

This review closes the remaining release-required ASVS logging and fail-safe
controls for the current Journeyman architecture.

## Security rejection logging

Journeyman records explicit audit events for authentication failures, login
rate limiting, CSRF rejection, authorization denial, session revocation, and
unexpected application errors. A central response hook additionally records
HTTP 400, 409, 422, and 429 responses as `security.control_rejected`, ensuring
that input-validation, business-logic, conflict, and anti-automation
rejections are represented even where a feature does not create a more
specific event.

Backend/security-control failures that affect authentication are logged and
audited. Directory revalidation failures fail closed for the request while
preserving the server-side session for later recovery. Unsafe fallback-admin
credential-file permissions are treated as a failed security control and do
not permit authentication.

## Log injection

The database audit log is structured JSON and therefore does not use
line-oriented interpolation for details. Conventional application logging also
installs `LogInjectionFilter` on Journeyman's application logger handlers.
Carriage-return and line-feed characters in message templates or formatting
arguments are escaped before rendering, preventing attacker-controlled text
from creating forged conventional log lines.

Secrets remain subject to the audit redaction rules in `app.services.audit`.

## Fail-closed review

The current security boundaries were reviewed for failure behavior:

- browser authentication defaults to anonymous when session state is missing,
  invalid, expired, revoked, or cannot be revalidated;
- directory revalidation outage denies the current protected request;
- disabled/deleted/replaced directory identities revoke the session;
- authorization denial returns 403;
- CSRF rejection returns 400 before the operation executes;
- runner API authentication failure returns 403;
- invalid/tampered encrypted credential material raises rather than returning
  plaintext or an empty secret;
- malformed/unsupported API bodies are rejected rather than coerced into
  privileged defaults;
- unexpected exceptions return a generic 500 response and are recorded
  server-side.

This is an application-level review. Database durability, reverse-proxy
availability, filesystem behavior, and external-provider failure semantics are
also constrained by their deployment configuration and the separate
Configuration/Secure Communication assessments.
