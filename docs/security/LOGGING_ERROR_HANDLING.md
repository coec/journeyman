# Security Logging and Error Handling

Journeyman uses two complementary logging channels. Security and administrative
audit events are persisted as structured rows in the `audit_log` database table.
Operational diagnostics and exception details are emitted through Flask's
application logger and are expected to be collected by the service manager or
deployment logging stack.

## Audit-log inventory

The database audit log records a UTC event timestamp, actor identity and role,
authentication mechanism, action, result, affected object metadata, source IP,
request correlation identifier, and structured JSON details. Audit details pass
through defensive key-based redaction before persistence. Passwords, secrets,
tokens, credentials, authorization values, cookies, CSRF values and similarly
named fields must not be stored in clear text in audit details.

Audit-log viewing is restricted to administrators. Journeyman exposes no UI or
HTTP route for editing or deleting individual audit rows. Database administrators
remain inside the deployment trust boundary and can modify the database directly;
therefore tamper-evident or append-only external retention is not currently
claimed.

Operational application logs include diagnostic messages and exception traces.
They are not intended to contain credentials or other secrets. Unlike the audit
table, these logs are currently conventional text logs rather than a single
application-wide structured format.

## Security events

Journeyman records successful and failed interactive authentication. HTTP 403
authorization failures are recorded as `authorization.denied` without sensitive
request bodies. CSRF rejections are recorded as `security.csrf_rejected` with
only request method and path metadata. Administrative and security-sensitive
operations use the existing audit service where implemented.

Coverage is not yet claimed for every validation failure, business-logic bypass
attempt, anti-automation event, or every external TLS/control failure. Those are
tracked as deferred ASVS work rather than inferred from generic application
logging.

## Error handling

Unexpected server errors are presented to the browser using a generic response.
The detailed exception is retained in the server-side application log and is not
placed in the HTTP response. A last-resort HTTP 500 handler exists so normal
production exception handling produces a controlled response rather than exposing
stack traces, SQL, tokens, keys or other internals.

Authentication and authorization errors fail closed. External dependency failures
are handled explicitly in several execution and inventory pathways, but Journeyman
does not yet claim a uniform circuit-breaker/graceful-degradation policy for every
external dependency.

## Deployment responsibilities and deferred controls

All hosts participating in Journeyman, its database, remote runners and external
logging infrastructure should use synchronized clocks. Journeyman stores audit
timestamps in UTC, but it does not currently verify host time synchronization.

Journeyman does not yet require secure forwarding of its audit/application logs
to a logically separate SIEM or log host. It also does not yet provide database-
level append-only/tamper-evident enforcement for the audit table. These controls
remain explicit deferred items in the ASVS matrix.

Application text logging also requires a future systematic log-injection review;
the structured database audit log avoids record-delimiter injection, but not every
operational log call has yet been proven to encode untrusted CR/LF characters.
