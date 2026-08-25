# Secure Communication

Journeyman treats transport security as part of the trust boundary between the
browser, the control plane, remote runners, directory services, inventory
providers, Git repositories and other configured backend services.

## Browser to Journeyman

The packaged web deployment terminates TLS in Nginx. The managed Nginx
configuration enables only TLS 1.2 and TLS 1.3 and proxies to Gunicorn over the
local loopback interface. If the optional HTTP listener is enabled it only
redirects to HTTPS; disabling that redirect does not create a plaintext
Journeyman listener.

The exact certificate trust model is deployment-specific. Journeyman is mainly
intended for internal enterprise/OT deployment and may legitimately use an
organization-managed private CA. ASVS requirements specifically requiring a
publicly trusted Internet certificate are therefore not treated as universally
applicable to Journeyman deployments.

The managed Nginx configuration pins the protocol floor to TLS 1.2/1.3,
configures an explicit forward-secret AEAD cipher list for TLS 1.2, disables
session tickets, and relies on the platform TLS 1.3 policy for TLS 1.3 cipher
selection. OCSP stapling and Encrypted Client Hello remain deployment/high-
assurance items and are still Deferred.

## Remote runners

Remote runners communicate with the control plane over HTTPS using normal TLS
certificate verification. A custom CA file may be configured for private PKI;
the runner still uses `ssl.create_default_context()` and does not disable
hostname or certificate validation.

The remote-runner agent now refuses registration, unregistration and normal
operation when `JOURNEYMAN_SERVER_URL` is not an `https://` URL. Embedded URL
credentials are also rejected. Runner authentication uses the registered runner
secret and per-dispatch token in addition to TLS, but it is not mutual TLS.

## LDAP / Active Directory

Enabled directory servers must use LDAPS. The LDAP client configures
`ssl.CERT_REQUIRED`, uses `ssl.PROTOCOL_TLS_CLIENT`, and may use a configured CA
certificate. Plain LDAP is rejected by directory-settings validation.

## Inventory providers and repositories

Satellite/Foreman and Zabbix support TLS certificate validation, and newly
created/edited inventory sources default to certificate verification where
applicable. However, Journeyman currently permits administrators to configure
HTTP endpoints or disable certificate validation for some inventory sources.
Git repository URLs may also use HTTP.

Those capabilities mean Journeyman cannot yet claim that *all* outbound
service-to-service traffic is encrypted and certificate-validated. Hardened
production deployments should use HTTPS and a trusted enterprise CA for these
services. Application-level enforcement is tracked as Deferred rather than
being assumed from deployment practice.

## Other service-to-service communication

The local Nginx-to-Gunicorn hop uses HTTP over `127.0.0.1`; it does not cross a
network interface. PostgreSQL transport security, when PostgreSQL rather than
SQLite is used, is deployment-specific and is not currently enforced by the
application configuration.

Journeyman does not currently use mutual TLS between its internal components.
Remote runners authenticate using high-entropy bearer credentials and dispatch
tokens over TLS. Controls that specifically require certificate-based mutual
service authentication remain Deferred.

## Review evidence

Automated security regression tests verify the managed TLS protocol floor,
remote-runner HTTPS enforcement and certificate-validating SSL context, LDAPS
validation, and rejection of plain LDAP. Known transport-security exceptions
are recorded explicitly in `ASVS_MATRIX.csv` rather than being represented as
fully compliant.
