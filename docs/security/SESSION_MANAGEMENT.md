# Session Management Security Model

This document records the browser-session model implemented by Journeyman and
forms part of the evidence for the OWASP ASVS 5.0.0 Session Management (V7)
assessment. Authentication is documented separately in `AUTHENTICATION.md`.

## Session mechanism

Journeyman uses Flask's signed, client-side session cookie for interactive human
sessions. The browser receives a self-contained session value containing the
authenticated Journeyman identity. Flask verifies the cookie signature on the
server using `SECRET_KEY` before the identity is accepted.

Journeyman does not use a database-backed reference-token store for interactive
browser sessions. Static API keys are not used as browser session identifiers.
Remote runner authentication is a separate machine-to-machine mechanism.

Production startup must have a unique `JOURNEYMAN_SECRET_KEY`. Journeyman refuses
to start in a non-debug configuration when the built-in development fallback
secret is still configured or the secret is empty. Compromise or reuse of the
production signing secret must be treated as compromise of browser sessions.

## Session creation and rotation

An authenticated browser session is created only after an explicit successful
`POST /login`. Before storing the authenticated identity, Journeyman calls
`session.clear()` and then creates the new authenticated session state. This
removes pre-authentication browser session data and causes a new signed session
cookie value to be issued after authentication.

Malformed or modified signed session cookies are rejected by Flask's backend
session verification and do not authenticate the request.

## Timeout model

Journeyman uses a per-user **sliding inactivity timeout** backed by the
server-side authentication-session registry. The default is 480 minutes
(8 hours). Users can change their own timeout under **Preferences** to any
value from 15 minutes through 7 days, using minutes, hours, or days.

`AuthSession.last_seen_at` is refreshed while an authenticated session is in
use. If the elapsed time since `last_seen_at` reaches the user's configured
timeout, the server-side session is revoked and the browser must authenticate
again. The browser's permanent-session cookie lifetime is 7 days so that it
does not prematurely defeat a longer user-selected idle timeout. The cookie is
still refreshed on active requests by Flask's default
`SESSION_REFRESH_EACH_REQUEST = True` behaviour.

An independent server-side **absolute maximum lifetime** remains in force even
for continuously active sessions. It defaults to 30 days and is configured
with `JOURNEYMAN_AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS`. This is deliberately
separate from the user-selectable inactivity timeout.

## Concurrent sessions

Journeyman currently permits an account to have an unlimited number of concurrent
browser sessions. No automatic action is taken when another session is created.
This behaviour is explicit rather than an accidental assumed limit.

Journeyman does not currently expose a user's active-session inventory, allow a
user to terminate other sessions, or allow an administrator to revoke all active
sessions for a specific user. Requirements for those capabilities are recorded as
Deferred in the ASVS matrix.

## Logout and session termination

Every authenticated page using the common application layout exposes a visible
**Sign out** control. Logout is a CSRF-protected POST operation which clears the
browser's current Flask session and returns the browser to the login page.

Because Flask's default session is a self-contained signed cookie, clearing the
current browser session does **not provide server-side revocation of a previously
copied valid cookie**. Similarly, disabling or deleting an Active Directory user
does not currently invalidate an already-issued Journeyman session immediately;
the identity and directory-derived role/group information remain in the signed
session until the session expires or is replaced.

For this reason Journeyman does not yet claim ASVS v5.0.0-7.4.1 or
v5.0.0-7.4.2. A future revocation design will require server-side session state,
a per-user/session revocation epoch, or another mechanism capable of rejecting
previously issued session cookies.

## Authentication-factor changes

Journeyman does not provide password, MFA, recovery-factor, email or telephone
change workflows. Authentication-factor lifecycle is owned by Active Directory,
so the application-specific factor-change workflow requirement is Not Applicable.
Journeyman currently has no directory callback which would revoke existing
Journeyman sessions when an external authentication factor changes.

## Sensitive operations and step-up authentication

Journeyman does not currently require a fresh authentication factor before highly
sensitive operations. Package confirmations and operational warnings are safety
controls, not re-authentication. ASVS v5.0.0-7.5.3 is therefore Deferred.

Journeyman also does not expose editable account-recovery/authentication
attributes, so the requirement to re-authenticate before modifying such
attributes is Not Applicable to the current application.

## Federated session management

Journeyman performs direct LDAP authentication and is not currently a SAML/OIDC
Relying Party participating in a federated browser-session ecosystem. Federated
session lifetime/termination controls are therefore Not Applicable.

A Journeyman browser session is created only after the user explicitly submits a
successful login request; simply visiting Journeyman or the login page does not
create an authenticated session.

## Deferred session work before release review

The current V7 assessment records the following material session-management gaps:

- define and enforce an absolute maximum browser-session lifetime;
- implement revocation of previously issued sessions after logout/termination;
- terminate active sessions when an account is disabled/deleted;
- provide administrator session revocation;
- provide users with active-session visibility and authenticated revocation;
- decide whether high-risk operations require step-up authentication.

These are deliberate Deferred findings and must be reviewed against the v1.0
release gate rather than being treated as framework-provided controls.
## Server-side revocation and absolute lifetime

Authenticated browser sessions now carry a random session identifier whose
server-side record is stored in the Journeyman database. The signed Flask
cookie remains responsible for integrity and browser transport, but a cookie is
accepted only while its server-side session record exists, has not been
revoked, and has not passed its absolute expiry.

Logout revokes the server-side record before clearing the browser cookie. A
previously copied valid cookie therefore cannot be replayed after logout.

The server-side registry also enforces the user's configured inactivity timeout.
An independent server-side maximum defaults to 30 days and is configured with
`JOURNEYMAN_AUTH_SESSION_ABSOLUTE_LIFETIME_SECONDS`. Continuously active
sessions therefore still require a new login after the absolute lifetime.

Immediate invalidation after Active Directory account disable/delete remains a
separate deferred control; Journeyman does not yet perform directory
revalidation on every authenticated request.

## Active Directory session revalidation

LDAP-authenticated browser sessions are periodically revalidated with the configured
directory service account. The default interval is 60 seconds and can be changed with
`JOURNEYMAN_AUTH_SESSION_DIRECTORY_REVALIDATION_SECONDS`. Revalidation confirms that
the account still resolves as an enabled AD user with the same objectGUID and still
has a Journeyman role. The current role and Team-group snapshot are refreshed at the
same time.

If the account is deleted, disabled, replaced, or loses all Journeyman role groups,
Journeyman revokes the server-side session and the browser must authenticate again.
If all configured directory servers are temporarily unavailable, the current request
fails closed but the session record is not revoked; subsequent requests retry the
directory check so redundant-directory recovery does not force unnecessary logins.
