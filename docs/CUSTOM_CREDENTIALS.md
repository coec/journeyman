# Custom credentials

Journeyman Custom credentials provide an AAP-style field-and-injector model for
credentials that do not fit the built-in Machine, Windows, Source Control,
Satellite, Vault, environment-variable, or Zabbix types.

A Custom credential has two parts:

- **fields** define the values that an administrator enters. A field may be
  marked `secret: true`; secret values are encrypted with the rest of the
  credential and are never repopulated into the edit form.
- **extra_vars** defines the Ansible variables injected when the credential is
  attached to a Project step. Values may contain only direct
  `{{ field_id }}` placeholders. Custom credentials do not evaluate arbitrary
  Jinja expressions.

Custom credential schemas are fixed after creation. To change a schema, create
or replace the credential deliberately rather than silently changing the
meaning of a credential already attached to Projects.

## Creating a Custom credential

1. Open **Credentials → Add Credential**.
2. Select **Custom**.
3. Enter the credential name, description and security scope.
4. Enter the field/injector definition as YAML and save it.
5. Journeyman opens **Edit Credential** with one input for each defined field.
6. Enter all values and save the credential.
7. Attach the Custom credential to the required Project step in the same way as
   another step credential.

The first implementation supports string fields only. Field IDs and injected
extra-variable names must be valid Ansible variable names.

## Example: RHV Multicluster Credential

The AAP-style RHV multicluster credential can be represented as:

```yaml
fields:
  - id: rhvm1_url
    type: string
    label: 'rhvm1 url (example: https://ovirt.example.com/ovirt-engine/api):'
  - id: rhvm1_user
    type: string
    label: 'Please provide rhvm1 username:'
  - id: rhvm1_passwd
    type: string
    label: 'Please provide rhvm1 password:'
    secret: true
  - id: rhvm2_url
    type: string
    label: 'rhvm2 url (example: https://ovirt.example.com/ovirt-engine/api):'
  - id: rhvm2_user
    type: string
    label: 'Please provide rhvm2 username:'
  - id: rhvm2_passwd
    type: string
    label: 'Please provide rhvm2 password:'
    secret: true
  - id: rhvm3_url
    type: string
    label: 'rhvm3 url (example: https://ovirt.example.com/ovirt-engine/api):'
  - id: rhvm3_user
    type: string
    label: 'Please provide rhvm3 username:'
  - id: rhvm3_passwd
    type: string
    label: 'Please provide rhvm3 password:'
    secret: true
  - id: rhvm4_url
    type: string
    label: 'rhvm4 url (example: https://ovirt.example.com/ovirt-engine/api):'
  - id: rhvm4_user
    type: string
    label: 'Please provide rhvm4 username:'
  - id: rhvm4_passwd
    type: string
    label: 'Please provide rhvm4 password:'
    secret: true
extra_vars:
  rhv1_url: '{{ rhvm1_url }}'
  rhv2_url: '{{ rhvm2_url }}'
  rhv3_url: '{{ rhvm3_url }}'
  rhv4_url: '{{ rhvm4_url }}'
  rhv1_passwd: '{{ rhvm1_passwd }}'
  rhv2_passwd: '{{ rhvm2_passwd }}'
  rhv3_passwd: '{{ rhvm3_passwd }}'
  rhv4_passwd: '{{ rhvm4_passwd }}'
  rhv1_username: '{{ rhvm1_user }}'
  rhv2_username: '{{ rhvm2_user }}'
  rhv3_username: '{{ rhvm3_user }}'
  rhv4_username: '{{ rhvm4_user }}'
```

When the credential is used, Journeyman materialises the rendered mapping into
a mode-0600 JSON extra-vars file in the private Job workspace and invokes
`ansible-playbook` with that file. Secret field values therefore do not appear
in the Project definition or command-line argument itself.

A Job fails before `ansible-playbook` starts if any Custom credential field has
no value.

## Security behaviour

- The schema and values are stored inside the encrypted credential payload.
- Secret fields are shown only through the existing owner-only **Reveal**
  operation and are covered by the same audit event as other credential
  reveals.
- Secret values are never pre-filled into the edit form. Leaving a secret field
  blank while editing retains the stored value.
- The injector is deliberately restricted to direct field placeholders; it is
  not a general Jinja execution surface.
- JXF/configuration exports continue to export only the credential requirement
  name and type. Custom schemas and values remain destination-local and are not
  exported.
- A Project step may use at most one Custom credential, consistent with the
  existing one-credential-per-type execution rule.
