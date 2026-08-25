# Authentication

Journeyman uses Active Directory over LDAPS for normal interactive authentication.
Directory groups determine the Administrator and User roles.

## Break-glass administrator

The local fallback administrator is an emergency recovery mechanism intended only
to restore normal Active Directory authentication.

A server administrator must explicitly provision a fresh activation with
`flask fallback-admin generate`.

Each activation has an immutable maximum lifetime of **60 minutes** from
provisioning. It cannot be extended from the browser. Signing out ends the
activation immediately, revokes every active fallback browser session, and a
server administrator must provision a new activation before fallback
authentication can be used again.

While a fallback administrator is signed in, Journeyman displays a persistent
expiry countdown and modal warnings after 30, 45, 50, and 55 minutes of the
activation lifetime. Browser-side timers are informational only; expiry is
enforced server-side against the persisted activation record and is also
processed by the scheduler.

At the 60-minute deadline the activation becomes unusable and all fallback
browser sessions are revoked. Re-provisioning replaces the password hash and
creates a new 60-minute activation.

Fallback provisioning, successful use, expiry, and logout remain represented in
the audit trail. The fallback account cannot change or extend its own activation
lifetime.
