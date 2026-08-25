# Web Frontend Security

Status: ASVS 5.0.0 V3 assessment completed.

Journeyman is a server-rendered Flask application intended to be deployed behind
the managed Nginx TLS reverse proxy. The browser is not a trusted security
boundary: authorization, CSRF protection, validation and state changes are
always enforced server-side.

## Required browser security features

Supported production browsers must support HTTPS/TLS, secure cookies,
`SameSite`, HSTS, Content Security Policy, `X-Content-Type-Options`, referrer
policy, and modern same-origin protections. Journeyman does not attempt to
provide a reduced-security compatibility mode for obsolete browsers. If a
browser cannot enforce the required mechanisms, that browser is outside the
supported production security model.

Production session cookies use the `__Host-` prefix, `Secure`, `HttpOnly`,
`SameSite=Lax`, `Path=/`, and no `Domain` attribute. Journeyman also enforces an
explicit serialized session-cookie size bound and clears an oversized session
rather than emitting a cookie that could be truncated inconsistently. Changing to the `__Host-`
name invalidates existing browser sessions once when this hardening is deployed.

## Response security headers

Journeyman emits a baseline browser policy on application responses and the
managed Nginx configuration adds HSTS at the TLS boundary. The baseline includes
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`,
`Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Resource-Policy: same-origin`, and a CSP that denies objects,
base-URI changes and framing.

Journeyman now generates a fresh cryptographic nonce for every request and
requires that nonce on inline script/style blocks. The CSP does not permit
`'unsafe-inline'`. Inline event-handler and style attributes are prohibited by
security regression tests; reusable behavior and presentation are kept in local
static assets. CSP violation reporting remains deferred.

## Cross-origin and request-forgery model

Journeyman does not expose a general cross-origin browser API and does not emit
permissive CORS policy. State-changing browser operations use non-safe HTTP
methods and CSRF tokens. The application does not use JSONP or `postMessage`.
Authenticated resources are marked `Cross-Origin-Resource-Policy: same-origin`.

## Client-side content and dependencies

Jinja autoescaping remains enabled and templates must not use `|safe` for
untrusted values. Static JavaScript/CSS dependencies are served locally rather
than loaded from third-party CDNs; therefore SRI is not presently required for
an external dependency. Any future external executable/style asset requires a
specific security review and SRI where applicable.

Journeyman does not use Flash, ActiveX, Java applets, Silverlight, browser
plugins or other obsolete client technologies.

## Redirects

The login `next` destination accepts only application-relative paths beginning
with a single `/`. Absolute URLs, protocol-relative URLs and script schemes are
replaced with the application root. Journeyman has no intended automatic
external-redirect workflow, so ASVS controls concerned with warning before an
intentional external navigation are not applicable to the current feature set.

## Deferred items

The following remain deliberate gaps rather than passed controls:

- CSP violation reporting;
- comprehensive automated DOM-clobbering analysis of client JavaScript;
- browser feature detection/blocking when required security mechanisms are
  unavailable;
- HSTS public preload registration, which is deployment/domain-specific and may
  not be appropriate for private OT hostnames.
