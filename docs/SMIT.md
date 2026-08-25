# Journeyman SMIT

`scripts/journeyman-smit` is a deliberately simple terminal, menu-driven Package launcher. It uses the normal `/api/v1` authentication, authorization, Package validation and dispatch path; it does not execute playbooks or scripts directly.

Set the Journeyman URL and an API token for the operator:

```bash
export JOURNEYMAN_URL=https://journeyman.example.org
export JOURNEYMAN_API_TOKEN='...'
/opt/journeyman/scripts/journeyman-smit
```

The menu lists only Packages the API token identity is authorized to dispatch. Prompted Package inputs are shown in Package order. Choice values, defaults, secret prompting, and `visible_when` / `required_when` dependencies are honoured before the final dispatch confirmation.

TLS verification is enabled by default. `--no-verify-tls` exists only for explicitly permitted test environments and must not be used to bypass production certificate validation.
