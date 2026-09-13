# Runner HTTPS/mTLS management transport

Journeyman v2 establishes a controller-to-runner management plane over HTTPS,
TCP/8443 by default.  This channel is separate from the public Journeyman web
endpoint. Runner job and Environment work remain pull-based, but PKI-enrolled
runners authenticate those HTTPS requests with their X.509 client identity.

The runner presents its Journeyman-issued runner certificate. Journeyman
verifies that certificate against the runner CA and performs normal TLS
hostname verification. Journeyman presents a separate controller-management
client certificate signed by the same CA. The runner requires a client
certificate and additionally requires the URI SAN
`urn:journeyman:controller`.

Before enabling the Patch 5 transport on an existing controller:

```
flask runner-pki status
flask runner-pki ensure-controller
flask db upgrade
```

Existing runners must then be updated/re-enrolled so their environment contains
`JOURNEYMAN_RUNNER_PRIVATE_KEY`, `JOURNEYMAN_RUNNER_CERTIFICATE`, and
`JOURNEYMAN_RUNNER_CA_CERTIFICATE`. The remote-runner service starts an HTTPS
listener on `0.0.0.0:8443` by default.

Network policy must allow the Journeyman controller to initiate TCP/8443 to
each remote runner. Journeyman does not automatically modify the runner host's
firewall because OT firewall scope and source restrictions are site-specific.

`journeyman_runner_management_port` can override 8443 in the runner management
playbook. It must be unique when more than one logical Journeyman runner is
hosted on the same operating-system instance.

The scheduler polls enrolled runners over the controller-to-runner mTLS channel
approximately every 30 seconds. Runner-to-controller heartbeat, Job claim,
Environment claim and result APIs use the runner certificate on the normal
Journeyman HTTPS endpoint.

## Certificate identity pinning and quarantine

Journeyman does not treat "signed by the runner CA" as sufficient identity for
an enrolled runner.  During each controller-to-runner health poll, the live TLS
peer certificate must also match the certificate recorded for that runner at
enrolment:

- certificate serial number;
- SHA-256 certificate fingerprint;
- URI SAN `urn:journeyman:runner:<runner_uuid>`;
- `CA:FALSE` basic constraint; and
- TLS server-authentication EKU.

Normal TLS validation continues to enforce the CA chain, validity period, and
hostname/IP SAN.  A certificate-validation or pinned-identity failure places
the runner in PKI quarantine.  Quarantined runners remain able to finish and
report already assigned work through the transitional runner API, but are not
eligible to claim new Jobs, execution slices, or Environment synchronization
work.  Ordinary connectivity failures do not quarantine a runner.

PKI quarantine is deliberately sticky.  A later successful connection does not
clear it.  The supported recovery is **Manage Remote Runner -> Update**, which
recognises the quarantine, performs a fresh one-time enrolment, generates a new
runner keypair/UUID/certificate, and clears the quarantine only after successful
certificate issuance.

## Automatic certificate renewal

Journeyman renews runner and controller management certificates before they
expire. Runner certificates are valid for 30 days by default and enter the
renewal window with 15 days remaining. Renewal keeps the existing runner UUID
and runner private key: the runner creates a CSR from its existing key over the
mTLS management channel, Journeyman signs it, and the runner atomically installs
the replacement certificate and current runner CA certificate.

Failed runner renewals are retried no more than once per day. After three
consecutive failures Journeyman raises a persistent administrator System
message and shows the failure count on the Runners page. A successful renewal
clears the failure state and warning.

The scheduler also renews the Journeyman-managed runner CA certificate at its
39-week renewal point while retaining the CA private key. The controller mTLS
client certificate follows the same 15-day renewal window as runner
certificates and also retains its existing private key.

Relevant configuration defaults are:

```text
JOURNEYMAN_RUNNER_CERTIFICATE_VALIDITY_DAYS=30
JOURNEYMAN_RUNNER_CERTIFICATE_RENEW_BEFORE_DAYS=15
JOURNEYMAN_RUNNER_CERTIFICATE_RETRY_SECONDS=86400
JOURNEYMAN_RUNNER_CERTIFICATE_WARNING_FAILURES=3
JOURNEYMAN_RUNNER_CA_VALIDITY_DAYS=364
JOURNEYMAN_RUNNER_CA_RENEW_AFTER_DAYS=273
```

## Runner-to-controller mTLS

From v2 Patch 8 onward, PKI-enrolled runners also present their runner
certificate when connecting to the Journeyman HTTPS endpoint. Nginx validates
that certificate against the Journeyman runner CA and forwards the verified
certificate to the local Gunicorn listener. Journeyman then pins the client
certificate to the registered runner UUID, serial number and SHA-256
fingerprint and requires the TLS client-authentication EKU.

The TLS layer uses `ssl_verify_client optional` so ordinary browser/API clients
do not need certificates. Runner API routes require a verified client
certificate for PKI-enrolled runners. A PKI-enrolled runner can never fall back
to its historical bearer secret. On the first successful mTLS API request,
Journeyman clears any transitional `api_secret_digest` retained from an earlier
version and records `runner.bearer_secret_retired` in the audit log.

Legacy runners which have never been PKI-enrolled may use the bearer path only
long enough to support a rolling update. Newly enrolled runners are never
issued a persistent bearer secret, and the runner environment no longer
contains `JOURNEYMAN_RUNNER_SECRET`.

Before updating a runner to the Patch 8 remote-runner binary, install the
updated constrained Nginx helper and apply the mTLS-aware web configuration:

```text
install -o root -g root -m 0755 scripts/journeyman-apply-web-settings \
  /usr/local/sbin/journeyman-apply-web-settings
flask runner-pki enable-web-mtls
```

The runner continues to connect to the normal Journeyman HTTPS URL (normally
TCP/443). The controller-to-runner management plane remains HTTPS/mTLS on
TCP/8443 by default.

## Manual bootstrap without SSH

A remote runner does not require an SSH path from the Journeyman control plane.
Resource administrators can use **Runners -> Manual Bootstrap** to generate a
self-contained shell script for a new or currently unregistered runner. The
script embeds the Journeyman runner executables and a one-time registration
token, but it does not bundle operating-system RPMs or Python packages.

The script must be transferred to the target through an administrator-approved
channel and run locally as root. It uses the target's configured RHEL-family
repositories to install Python, cryptography and Ansible prerequisites, creates
the `journeyman` service account and systemd unit, generates the runner private
key on the target, submits a CSR to Journeyman over HTTPS, and starts the runner.
The generated registration token is valid for one hour and is consumed by the
first successful enrolment. The script deletes itself after successful
bootstrap.

Optional `https_proxy`, `no_proxy`, and a path to a Journeyman web-server CA
certificate already present on the target can be embedded when the package is
generated. Proxy credentials, if included in the URL, therefore make the
bootstrap script sensitive and it must be handled accordingly.
