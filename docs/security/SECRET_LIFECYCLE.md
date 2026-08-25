# Secret lifecycle

Journeyman separates secrets it owns from credentials whose lifecycle is owned
by another system. The application must not create availability failures by
silently expiring a remote-system credential that only the remote system can
replace.

| Secret | Lifecycle | Enforcement |
|---|---|---|
| Break-glass administrator | Maximum 60 minutes | Hard server-side expiry; logout immediately expires the activation and revokes all fallback sessions. |
| Journeyman API bearer token | Maximum 12 months | Hard expiry. The owner receives a persistent web warning and API response warning headers during the final 30 days. |
| Session-signing key | Minimum 7 days; desired maximum 12 months | `journeyman.service` rotates it after an eligible server reboot when server uptime is under five minutes and the key is at least seven days old. It rotates at most once per boot. Administrators receive an overdue warning after 12 months. |
| Credential-encryption key | Rotate within 12 months | Administrator warning begins 30 days before due. Rotation is explicit through `flask credential-key rotate` because re-encryption is an administrative operation. |
| Signal Source HMAC secret | Rotate within 12 months | Administrator warning begins 30 days before due. Rotation is coordinated with the external sender; Journeyman does not automatically disable inbound monitoring at the deadline. |
| External-system Credential | Source-system policy | Journeyman does not impose expiry. Credentials unchanged for more than 12 months are marked "may be too old" so an administrator can verify the source system's policy. |
| Runner/bootstrap registration secret | One-time/short-lived | Existing bootstrap controls apply; registration secrets are not long-lived application credentials. |

## Session-signing key coordination

Only `journeyman.service` is enabled on a main Journeyman server. It is a
one-shot lifecycle coordinator that disables independent boot enablement for the
four child units, prepares/rotates shared key material, and then systemd starts:

- `journeyman-web.service`
- `journeyman-scheduler.service`
- `journeyman-runner.service`
- `journeyman-environment-builder.service`

The child units are static/disabled and use `PartOf=journeyman.service`, so
stopping or restarting the parent controls the complete main-server application.
The remote-runner unit is intentionally independent.

At startup, the coordinator rotates the session-signing key only when system
uptime is less than five minutes, the current key is at least seven days old,
and the current boot ID has not already performed a rotation. This avoids key
churn during ordinary service restarts or repeated reboots.

A server administrator can perform an explicit runtime rotation with:

```text
/opt/journeyman/bin/journeyman-service-coordinator rotate-session-key
```

The coordinator atomically replaces the key and sends SIGHUP to each active
main-server child service. Gunicorn reloads its workers; the remaining child
services explicitly accept SIGHUP without treating it as a shutdown request.
Rotation invalidates browser sessions and CSRF state but does not re-encrypt or
invalidate stored Credentials, API bearer tokens, runners, or Signal Sources.

Key metadata contains timestamps, boot ID and a non-secret SHA-256 fingerprint;
it never contains the key itself.
