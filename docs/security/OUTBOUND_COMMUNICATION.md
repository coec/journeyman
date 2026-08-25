# Outbound Communication Security

Production Journeyman separates destination authorization from web-admin
configuration. `JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS` is a sysadmin-owned
allowlist loaded from the service environment. Web administrators cannot add a
new Git, Satellite, Zabbix, or build-proxy destination unless its hostname is
already permitted by that allowlist.

Entries are exact hostnames, `*.example.com` suffixes, or either form with an
explicit `:port`. A global `*` entry is deliberately not supported. Literal
loopback, link-local, unspecified, and multicast IP destinations are rejected.
Private RFC1918 addresses remain valid when explicitly allowlisted because
Journeyman is designed for internal infrastructure networks.

HTTP-based Git, Satellite, Zabbix, and build-proxy URLs must use HTTPS and
may not embed HTTP credentials. Git repositories may alternatively use SSH,
either as `ssh://git@host/path.git` or the common SCP-style
`git@host:path.git`; the SSH hostname is checked against the same sysadmin-
owned outbound allowlist. Plaintext `http://` and `git://` repository
transports are not permitted in production. Satellite and Zabbix certificate
validation cannot be disabled. Private PKI is supported through the operating
system trust store; install the organization's issuing CA there rather than
disabling validation.

Git HTTP redirects are disabled. Zabbix redirects are rejected. Provider and
Git destinations are validated again at execution time so legacy database
rows cannot bypass current policy.

`DevelopmentConfig` does not enforce the destination allowlist to keep isolated
unit/development workflows practical. Packaged `ProductionConfig` always does.
## Message-size bounds

Administrator-configured outbound HTTP/HTTPS URLs are limited to 2048
characters before parsing or use. Generated authentication header values are
bounded to 8192 bytes and reject carriage-return/newline characters. These
limits are enforced centrally so webhook, inventory, repository and other
backend integrations cannot generate unbounded HTTP request targets or headers.
