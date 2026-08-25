# Token, OAuth/OIDC, and WebRTC Applicability

This document records the Journeyman applicability decision for OWASP ASVS
5.0.0 chapters V9 (Self-contained Tokens), V10 (OAuth and OIDC), and V17
(WebRTC).

## V9 Self-contained Tokens

V9 is **partially applicable**. Journeyman does not use JWT access tokens, ID
Tokens, SAML assertions, or another generic bearer-token framework for browser
authentication, but Flask's interactive browser session is a signed,
self-contained cookie. The cookie contains authenticated session state and is
therefore assessed as a self-contained token rather than declaring the whole
chapter Not Applicable.

The current controls are:

- Flask verifies the session cookie MAC before its contents are accepted. A
  regression test deliberately modifies the signature and verifies that the
  request is not authenticated.
- The signing mechanism and verification key are selected server-side by the
  Flask session implementation. The cookie cannot supply an algorithm or an
  alternate key-discovery URL/header for the verifier to trust.
- Permanent session cookies are timestamped and are accepted subject to the
  configured session lifetime. Journeyman currently uses an eight-hour sliding
  inactivity lifetime as documented in `SESSION_MANAGEMENT.md`.
- The browser authentication path consumes only the Flask session cookie as a
  browser session; it does not accept a generic token supplied as another token
  type and reinterpret it as a session.
- The production signing secret must be unique to a Journeyman deployment and
  is not intended to sign tokens for unrelated audiences.

### Deferred audience restriction

ASVS v5.0.0-9.2.3 remains **Deferred**. The Flask session format does not carry
an explicit cryptographic audience claim comparable to a JWT `aud` claim.
Journeyman mitigates cross-context use through a deployment-specific secret and
the `__Host-journeyman_session` cookie boundary, but those controls are not an
explicit audience field in the token itself.

If Journeyman later introduces JWTs or another independently consumable
self-contained token, the whole V9 assessment must be revisited rather than
assuming the Flask-session findings transfer to that token format.

## V10 OAuth and OIDC

V10 is **Not Applicable** to the current architecture.

Journeyman does not currently act as an:

- OAuth client;
- OAuth resource server accepting OAuth access tokens;
- OAuth authorization server;
- OIDC relying party/client;
- OpenID Provider; or
- OAuth/OIDC consent-management service.

Interactive users authenticate directly against configured LDAP/Active
Directory services, with a separately documented local fallback-administrator
path. Remote runners use Journeyman-specific machine credentials and dispatch
secrets, not OAuth access/refresh tokens.

Adding OAuth 2.x or OpenID Connect support is an ASVS review trigger and requires
reassessment of all V10 controls before that feature is considered complete.

## V17 WebRTC

V17 is **Not Applicable** to the current architecture.

Journeyman does not provide WebRTC functionality and does not operate a TURN
server, WebRTC media server, DTLS-SRTP media path, RTP/SRTP recording service,
or WebRTC signaling service.

Introducing browser audio/video/data-channel functionality, TURN/STUN
infrastructure, or WebRTC signaling is an ASVS review trigger and requires a
fresh V17 assessment.

## Review rule

`Not Applicable` in this assessment means the technology or role is absent from
the current Journeyman architecture. It is not a permanent exemption. Any
future feature which introduces OAuth/OIDC, generic self-contained tokens, or
WebRTC must reopen the corresponding chapter before release.
