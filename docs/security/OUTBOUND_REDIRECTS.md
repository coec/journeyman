# Outbound Redirect Policy

Journeyman does not follow redirects for administrator-configured outbound
service destinations.

This is part of the same trust boundary as the sysadmin-owned outbound host
allowlist: allowing an approved URL to redirect to an arbitrary second URL
would otherwise bypass the destination policy.

## Git repositories

Git commands run with `http.followRedirects=false`.

## Zabbix

The Zabbix HTTP client uses an explicit `HTTPRedirectHandler` that rejects
redirect responses.

## Satellite / Foreman inventory

The upstream `theforeman.foreman.foreman` inventory plugin uses Python
Requests and does not expose a redirect-control option. Its Requests session
therefore follows redirects by default.

Journeyman runs that inventory plugin only through the dedicated
`ansible-inventory` subprocess. For that subprocess Journeyman creates a
private mode-0700 directory containing a mode-0600 `sitecustomize.py`, prepends
the directory to `PYTHONPATH`, and patches `requests.sessions.Session.request`
so `allow_redirects` is always false. Any 3xx response raises an error. The
temporary import-hook directory is deleted after the subprocess exits.

The patch is deliberately scoped to the Satellite inventory subprocess; it
does not modify the installed Foreman collection or globally monkey-patch the
Journeyman web process.

Any future outbound HTTP client must either reject redirects or explicitly
validate every redirected destination against the outbound destination policy
before following it.
