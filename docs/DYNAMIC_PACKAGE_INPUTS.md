# Dynamic Package inputs from inventory host variables

Journeyman can use resolved inventory host variables to populate a Package Choice
input and to display contextual help beside another input. This enables a
workflow such as:

**Zabbix LLD → dynamic inventory → dependent Package choice → current-state
help → Ansible Project**.

These are two independent mechanisms:

- `choices_from_hostvar` produces selectable values from a list of objects in
  the selected host's inventory variables.
- `help_from_hostvar` looks up an inventory item associated with a previously
  selected choice and displays its value as **read-only help**. It does not set
  the input value or change what the Package submits.

## Example: manage an Oracle tablespace

This example uses a Zabbix inventory containing discovered Oracle tablespace
items, such as:

```text
oracle.tablespace.size[SYSAUX,size]
oracle.tablespace.usage[SYSAUX,usage]
```

The size item has a `last_value` in **bytes**, along with a Zabbix
`last_clock` timestamp. The goal is to select `SYSAUX` once, then see its
current size when entering a new size.

### 1. Configure the Zabbix inventory

In **Inventories**, edit the Zabbix dynamic inventory used by the Project.
Enable retrieval of additional selected items, and configure an item-key
pattern matching `oracle.tablespace.size*`. Enable LLD-discovered items
and latest values. Refresh or preview the inventory and confirm the intended
host has a nonempty `zabbix.selected_items` list with `key`, `last_value`, and
`last_clock` fields.

Limit the patterns to the items needed by the Package: item retrieval is
opt-in, and the configured per-host limit constrains the resulting list, **not**
the initial Zabbix API response size. Large Zabbix estates should validate
refresh cost before using broad patterns.

### 2. Confirm the Project inventory

Open **Projects → 13 - Manage Tablespaces → Edit**. The Project must
resolve the Zabbix inventory that contains the selected items. A preview of a
different inventory does not make its host variables available to this
Package.

The dynamic lookup uses the **exact inventory hostname**; it does not infer a
host from a cluster or search all hosts in a group.

### 3. Configure the Package inputs

Open **Packages → 13- Manage Tablespace → Edit** and use this order:

| Input variable | Label | Type | Notes |
| --- | --- | --- | --- |
| `var_clustername` | Clustername | Choice | Existing cluster selection and inventory binding |
| `var_hostname` | Server | Text | Exact hostname from the Project inventory |
| `var_tablespace` | Tablespace name | Choice | No static choices; dynamic validation below |
| `var_size` | New size | Text | Keep empty until the operator enters a new size |

All four inputs should retain their appropriate runtime-variable bindings.
The host and tablespace inputs must precede the inputs that depend on them.

For **Tablespace name** (`var_tablespace`), enter under **Validation**:

```yaml
choices_from_hostvar:
  host_input: var_hostname
  path: zabbix.selected_items
  value_key: key
  key_pattern: '^oracle\.tablespace\.size\[([^,]+),size\]$'
  extract_group: 1
  distinct: true
  sort: true
```

The regular expression matches only `size` items, captures the tablespace name
in group 1, and uses the extracted name as **both the displayed label and the
submitted value**. `distinct` removes duplicate names; `sort` orders them.
For example, the dropdown displays `SYSAUX`, not `Tablespace SYSAUX size` or
the full Zabbix key.

For **New size** (`var_size`), keep the ordinary static help text, e.g.
`e.g. 10000 (in MB)`, and enter under **Validation**:

```yaml
help_from_hostvar:
  host_input: var_hostname
  match_input: var_tablespace
  path: zabbix.selected_items
  value_key: last_value
  key_pattern: '^oracle\.tablespace\.size\[([^,]+),size\]$'
  match_group: 1
  format: bytes_to_mib
  prefix: "Current size: "
  suffix: " MiB"
```

This matches the chosen tablespace against the same discovered item keys,
reads `last_value`, and converts bytes to **MiB** (1 MiB = 1,048,576 bytes).
When `last_clock` is available, the display also includes the Zabbix
measurement time in UTC. The displayed measurement may be stale; it is not a
live query at the moment of execution.

### 4. Test the dispatch form

1. Open the Package's **Dispatch** form.
2. Select the cluster, then enter the exact server hostname (for example,
   `devapp10.scada.horizonpower.com.au`). Move focus out of the hostname field
   if the dependent choice has not refreshed yet.
3. Open **Tablespace name** and select `SYSAUX`.
4. Confirm **New size** shows a contextual current-size message with a Zabbix
   timestamp, while the input itself remains blank.
5. Enter the intended new size only when actually dispatching the resize.

An example submitted variable set is:

```yaml
var_clustername: dev01
var_hostname: devapp10.scada.horizonpower.com.au
var_tablespace: SYSAUX
var_size: "10000"
```

`var_size` is **never** automatically populated with the current monitored
size. This prevents an informational lookup from silently becoming an
execution parameter. If the matching monitoring item is absent or cannot be
read, the static help text remains the fallback.

## Field reference

### `choices_from_hostvar` (Choice inputs)

| Field | Purpose |
| --- | --- |
| `host_input` | Earlier input containing the exact inventory hostname |
| `path` | Dot-separated path to a list of hostvar objects |
| `value_key` | Object field used as the source value |
| `label_keys` | Optional object fields used for labels without extraction |
| `key_pattern` | Optional regular expression applied to `value_key` (full match) |
| `extract_group` | Optional regex capture group to use as value and label |
| `distinct` | Optional deduplication (defaults to true) |
| `sort` | Optional sorting by displayed label |

### `help_from_hostvar` (display-only contextual help)

| Field | Purpose |
| --- | --- |
| `host_input` | Earlier input containing the exact inventory hostname |
| `match_input` | Earlier input containing the selected resource identifier |
| `path` | Dot-separated path to the list of hostvar objects |
| `key_pattern` | Full-match regular expression applied to each object's `key` |
| `match_group` | Capture group containing the resource identifier (default 1) |
| `value_key` | Field holding the help value, such as `last_value` |
| `format` | `raw` or `bytes_to_mib` |
| `prefix`, `suffix` | Optional text around the formatted value |

Regular expressions must pass Journeyman's safe-regex validation. Invalid
patterns or unsupported fields are rejected when the Package is saved.

## Reactions

A Package intended for reaction dispatch must have **Allow as Reaction**
enabled (`allow_as_reaction: true`) and the appropriate Reaction configuration.
The example Package originally has this setting disabled. See
[Reactions](REACTIONS.md) for reaction setup and dispatch semantics.

The operator-facing dynamic choice and help mechanisms are useful during
manual dispatch. They **do not** automatically populate reaction inputs or
translate a Zabbix event into a tablespace name. A future reaction must
explicitly supply a valid `var_tablespace` (and any other required values),
and normal Package/Project validation and authorization still apply.

## Troubleshooting

- **Empty tablespace dropdown:** verify the Project's resolved inventory, exact
  hostname, `zabbix.selected_items` path, item-key pattern, and whether the
  inventory refresh included latest selected items.
- **Full item key instead of tablespace name:** check `extract_group: 1` and
  that the regex matches the intended `size` key.
- **Missing current-size help:** confirm `var_hostname` and `var_tablespace`
  match an item in the resolved hostvars and that `last_value` is present.
- **Size looks different from Zabbix:** check bytes-versus-MiB conversion;
  the example uses binary MiB, not decimal MB.
- **Slow inventory refresh:** narrow item patterns and measure Zabbix API
  response size; the per-host limit does not bound the initial API response.
