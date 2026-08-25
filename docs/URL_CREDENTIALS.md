# URL / API credentials

Journeyman v1.1 introduces the provider-neutral **URL / API** credential. The endpoint and its authentication material now travel together instead of adding a new Credential type for every inventory provider.

Supported authentication modes are:

- none
- HTTP Basic (`username` + encrypted password)
- bearer token
- token header (for APIs such as NetBox that use `Authorization: Token ...`)
- OAuth 2 client credentials (`username` is the client ID; encrypted password is the client secret)

The credential can also store an OAuth token URL, scope, and token prefix. Passwords, client secrets and tokens remain encrypted in the existing Credential store.

## Provider conventions

- **Red Hat Satellite:** base Satellite URL, HTTP Basic authentication.
- **Zabbix:** base Zabbix URL, bearer token authentication. Existing `zabbix` credentials remain readable for compatibility and can be migrated.
- **NetBox:** base NetBox URL, token authentication with the `Token` prefix.
- **Red Hat Lightspeed:** base URL `https://console.redhat.com`, OAuth 2 client credentials, with the Red Hat SSO token endpoint and service-account scope appropriate to the deployment.

## Migrating legacy credentials

Preview the migration:

```bash
/opt/journeyman/venv/bin/python scripts/migrate_url_credentials.py
```

Commit it only after reviewing the plan:

```bash
/opt/journeyman/venv/bin/python scripts/migrate_url_credentials.py --apply
```

Satellite credentials are converted in place. A Zabbix credential used against one endpoint is also converted in place and the duplicate endpoint is removed from the Inventory record. If one legacy Zabbix token is shared across several different endpoints, the migration creates endpoint-specific URL credentials and rewires each Inventory so provider semantics are preserved.
