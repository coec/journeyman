# Reactions

Journeyman reaction automation uses four deliberately distinct concepts:

```text
Source
  ↓
Signal
  ↓
Reactor
  ↓
Reaction
```

## Phase-one scope

Phase one intentionally supports only:

- Zabbix Sources submitting directly to Journeyman over HTTPS.
- Syslog selected and parsed by existing rsyslog/syslog-ng infrastructure, then forwarded through a Journeyman remote Runner.
- SNMP Traps
- Packages as the only Reaction target.
- Reactor modes `Observe` and `Automatic`.
- Reaction inputs mapped from Signal fields or constants.
- Per-Reactor cooldown and concurrency controls.
- Optional delayed Automatic Reactions with correlated recovery-Signal suppression.

Journeyman does not implement a syslog listener or parser. Reactors cannot execute Projects, playbooks, shell commands, Python or Jinja directly.

## Reaction Packages

A Package is unavailable to Reactors unless an administrator explicitly enables:

```text
Allow as Reaction
```

The setting is off by default and is treated as trusted local state. JXF export does not include it and JXF import rejects attempts to supply it. Imported Packages are forced to `Allow as Reaction = false`.

A Reactor still passes all mapped values through the normal Package input validation, inventory binding and execution-preview machinery.

## Zabbix Source authentication

Each Zabbix Source receives:

- a public Source UUID;
- a unique HMAC secret shown only when created or regenerated;
- an explicit sender-IP/CIDR allowlist.

Journeyman requires HTTPS and validates the request timestamp within five minutes. The HMAC input is exactly:

```text
source_uuid + "\n" + unix_timestamp + "\n" + raw_json_body
```

with HMAC-SHA256 represented as lowercase hexadecimal.

Headers:

```text
X-Journeyman-Source: <source UUID>
X-Journeyman-Timestamp: <Unix epoch seconds>
X-Journeyman-Signature: <HMAC-SHA256 hex>
```

Zabbix's JavaScript environment supports HMAC-SHA256, so the media type can sign the request without storing a Journeyman username/password.

A Signal is unique by `(Source, signal_id)`. Re-submission of the same identifier is acknowledged as a duplicate rather than creating another Signal.

### Zabbix Signal schema v1

```json
{
  "schema_version": 1,
  "signal_id": "938274",
  "signal_type": "problem",
  "timestamp": "2026-08-12T14:20:00+08:00",
  "host": "dbprod04.example",
  "severity": "warning",
  "description": "Tablespace usage above threshold",
  "fields": {
    "tags": {
      "tablespace": "USERS"
    },
    "values": {
      "usage_percent": 91.7
    }
  }
}
```

The Source supplies facts. It never supplies a Package name or executable action.

## Syslog through a Runner

The path is:

```text
Device/application
      ↓
rsyslog or syslog-ng
      ↓
journeyman-signal-spool
      ↓
/var/spool/journeyman/signals/<source-uuid>/*.json
      ↓
Journeyman remote Runner
      ↓
Journeyman
```

`journeyman-signal-spool` is not a syslog implementation. It accepts normalized JSON lines on stdin and atomically persists each line as one spool file.

The Runner submits batches using its existing Runner UUID/secret. A Syslog Source is assigned to one specific remote Runner; credentials belonging to another Runner cannot submit Signals for it.

The Runner removes a spool file only after the server returns `accepted` or `duplicate`. A connectivity/server failure leaves the file in place for retry. A permanently rejected record is moved to the Source's `rejected/` directory for inspection rather than retried indefinitely.

Example normalized record:

```json
{
  "signal_id": "router17-1786520000123456789",
  "timestamp": "2026-08-12T14:33:20+08:00",
  "sender_ip": "192.0.2.17",
  "host": "router17",
  "severity": "warning",
  "description": "Interface Gi0/1 transitioned down",
  "fields": {
    "facility": "local4",
    "program": "ios"
  }
}
```

The syslog daemon is responsible for selecting messages and producing the JSON record. The filtering rules are site-specific, but the following is a complete rsyslog example that produces the format expected by Journeyman.

### rsyslog example

The Journeyman remote Runner installs the spool helper at:

```text
/opt/journeyman/bin/journeyman-signal-spool
```

Create an rsyslog configuration such as `/etc/rsyslog.d/60-journeyman-signals.conf`. Replace `<SOURCE-UUID>` with the UUID shown for the Syslog Source in Journeyman:

```rsyslog
module(load="omprog")

template(name="JourneymanSignal" type="list") {
    constant(value="{")
    property(outname="signal_id" name="uuid" format="jsonf")
    constant(value=",")
    property(outname="timestamp" name="timereported" dateFormat="rfc3339" format="jsonf")
    constant(value=",")
    property(outname="sender_ip" name="fromhost-ip" format="jsonf")
    constant(value=",")
    property(outname="host" name="hostname" format="jsonf")
    constant(value=",")
    property(outname="severity" name="syslogseverity-text" format="jsonf")
    constant(value=",")
    property(outname="description" name="msg" format="jsonf")
    constant(value=",\"fields\":{")
    property(outname="facility" name="syslogfacility-text" format="jsonf")
    constant(value=",")
    property(outname="program" name="programname" format="jsonf")
    constant(value=",")
    property(outname="input" name="inputname" format="jsonf")
    constant(value="}}\n")
}

# Example selector only. Replace this with the messages that should become
# Journeyman Signals. Do not add "stop" unless these messages should also be
# prevented from reaching the normal rsyslog destinations that follow.
if $syslogfacility-text == "local4" then {
    action(
        type="omprog"
        binary="/opt/journeyman/bin/journeyman-signal-spool <SOURCE-UUID>"
        template="JourneymanSignal"
    )
}
```

`omprog` keeps the helper running and feeds the selected records to its stdin. The template deliberately emits one complete JSON object followed by a newline because `journeyman-signal-spool` consumes one JSON object per input line.

The example uses rsyslog's per-message `uuid` property as `signal_id`. This is convenient because Journeyman requires `signal_id` to be unique within a Source. The `uuid` property is available only when rsyslog was built with UUID support. If it is unavailable on the target system, use a stable unique event identifier supplied by the device/application instead; do not use a value that can repeat, because Journeyman treats a repeated `(Source, signal_id)` as a duplicate Signal.

The important mappings are:

```text
Journeyman field     rsyslog property
------------------   ----------------------
signal_id             uuid
timestamp             timereported
sender_ip              fromhost-ip
host                   hostname
severity               syslogseverity-text
description            msg
fields.facility        syslogfacility-text
fields.program         programname
fields.input           inputname
```

You can check for `uuid` by running `rsyslogd -v | grep uuid`. It is enabled by default on RHEL8 and above.

`sender_ip` is mandatory. It is also checked against the sender IP/CIDR allowlist configured on the Syslog Source, so it should represent the original syslog sender rather than the Runner itself.

Before restarting rsyslog, validate the configuration:

```bash
rsyslogd -N1
```

Then restart or reload rsyslog using the site's normal service-management procedure. To test a `local4` selector locally:

```bash
logger -p local4.warning -t journeyman-test 'Test Signal from rsyslog'
```

A locally generated test normally has a loopback/local sender address. The Syslog Source must permit that address for the test to be accepted; production sender allowlists should remain limited to the real devices or networks that are expected to submit Signals.

If rsyslog reports that it cannot execute the helper or write the spool, check the rsyslog service's OS permissions and SELinux/systemd restrictions. `omprog` executes the program in the rsyslog service context, while the spool helper must be able to create files beneath `/var/spool/journeyman/signals/<source-uuid>/`.

## Reactor matching

A Reactor uses one top-level `ALL` or `ANY` group of declarative rules. Supported operators are:

- equals / not equals
- contains
- starts with / ends with
- exists / does not exist
- greater than / greater than or equal
- less than / less than or equal

Available paths include common fields such as:

```text
host
severity
description
signal_type
external_signal_id
fields.tags.tablespace
fields.values.usage_percent
```

No arbitrary expressions are evaluated.

## Reaction input mapping

Each Package input can be left to its normal Package default/conditional behaviour, or mapped from:

- a Signal field;
- a constant.

Example:

```text
hostname
    ← host

tablespace
    ← fields.tags.tablespace

growth_gb
    ← constant: 10
```

The resulting values are validated by the Package exactly as a human-supplied launch value would be. Secret/password Package inputs cannot be populated by a Reactor.

### Mapping extraction patterns

A Signal-field mapping may optionally define a regular-expression extraction pattern. If the pattern contains capture groups, the first captured group becomes the mapped value; otherwise the complete match is used. This is useful when a device name, interface, tablespace or other correlation value is embedded inside a longer description.

For example, given:

```text
Interface Tu25111 (Tunnel to remote site): Link down
```

a mapping for `interface` can use:

```text
Signal field: description
Pattern:      Interface\s+([^(]+)
```

to resolve `interface` as `Tu25111`. The same extraction is reused when a recovery Signal is correlated with a pending Reaction.

## Observe mode

Observe is the default mode for new Reactors.

A matching Signal causes Journeyman to:

1. resolve Reaction inputs;
2. run normal Package input validation;
3. persist the would-be Reaction and its resolved non-secret inputs;
4. queue no Job.

This allows a Reactor to be commissioned against real Signals before automatic action is enabled.

## Automatic mode

Automatic mode performs the same matching and validation, then builds the normal Journeyman execution preview and queues the Package through the existing execution engine.

The Job records the Reactor as its requester and the Reaction links the Source Signal, Reactor, Package and resulting Job.

## Recovery windows

An Automatic Reactor can optionally delay execution for a configured recovery window. During that period the Reaction is persisted with status `pending` and no Job is created. This does not occupy a Runner or an execution slot.

If a matching recovery Signal arrives before the window expires, Journeyman suppresses the pending Reaction instead of running the Package. The Reaction remains visible for audit purposes and records the recovery Signal that suppressed it.

A recovery configuration consists of:

- a recovery window in seconds (`0` disables delayed execution);
- one top-level `ALL` or `ANY` recovery match group using the normal Reactor rule operators;
- one or more Package input names used to correlate the recovery Signal with the original trigger.

Correlation inputs must already be mapped from Signal fields. Journeyman resolves those mappings against both the original trigger and the recovery Signal, including any configured extraction regex, then compares the resulting values. A recovery Signal only suppresses pending Reactions whose selected correlation values all match.

For a router interface example:

```text
Trigger match:
    description contains "link down"

Reaction input mappings:
    hostname  <- host
    interface <- description
                 pattern: Interface\s+([^(]+)

Recovery window:
    120 seconds

Recovery match:
    description contains "link up"

Correlate using:
    hostname, interface
```

A `link up` for another router or another interface therefore cannot suppress the pending Reaction. If no correlated recovery Signal arrives before the deadline, the Journeyman Runner releases the pending Reaction and queues the Package through the normal execution engine. Because the deadline is stored in the database, the recovery window survives a Journeyman restart.

Recovery matching is evaluated before normal trigger matching for each Signal. A recovery Signal can therefore suppress an existing pending Reaction even if that same Signal does not satisfy the Reactor's trigger rules.

## Safety controls

Phase one provides:

- Source-level sender restrictions;
- Signal deduplication;
- Observe commissioning mode;
- per-Reactor cooldown by affected host;
- per-Reactor maximum concurrent queued/running/cancelling Jobs;
- optional database-backed recovery windows with explicit correlation fields;
- Package opt-in and normal Package input validation.

Approval queues are deliberately omitted from phase one.

## Reaction development best practice

Before creating a Reactor, ensure that the Package to be executed has been configured to **Allow as Reaction**.

A recommended development workflow is:

1. **Define the Signal Source**

   Configure the appropriate Source, such as Zabbix, syslog, or an SNMP trap source.

2. **Observe incoming Signals**

   Allow representative events to arrive and use **Inspect** on the Signals page to understand the data Journeyman receives.

   For example, a Zabbix Signal may contain:

   ```text
   {
     "tags": {
       "class": "network",
       "component": "network",
       "description": "To wireless AP",
       "interface": "Gi0/15",
       "scope": "availability",
       "target": "cisco-ios"
     },
     "zabbix": {
       "event_id": "303125048",
       "event_nseverity": "3",
       "event_opdata": "Current state: down (2)",
       "event_status": "PROBLEM",
       "event_value": "1",
       "host_id": "27418",
       "host_name": "dc_swi_01",
       "trigger_id": "273816"
     }
   }
   ```

   Inspecting real Signals is preferable to guessing field names or relying on assumptions about the sending system.

3. **Create the Reactor**

   Once the relevant Signal structure is understood, create a Reactor and define the **Signal Matching** criteria using the fields required to identify the event.

   For example:

   ```text
   fields.tags.class             equals       network
   fields.tags.target            equals       cisco-ios
   fields.zabbix.event_opdata    starts with  Current state: down
   ```

   Match only the fields required to uniquely identify the condition. Avoid unnecessarily broad matching that may cause unrelated Signals to produce Reactions.

4. **Map Reaction inputs**

   Map values from the Signal into the Package inputs required by the Reaction.

   Where a required value is embedded within a larger Signal field, use the mapping pattern support to extract only the required portion.

5. **Test in Observe mode**

   Initially configure the Reactor in **Observe** mode.

   Generate or wait for representative Signals and inspect the resulting Reactions to confirm that:

   * the intended Signals match;
   * unrelated Signals do not match;
   * Package input mappings resolve to the expected values;
   * any regular-expression extraction behaves correctly.

   Observe mode allows the complete matching and input-resolution behaviour to be tested without executing the Package.

6. **Configure recovery behaviour where appropriate**

   For transient conditions, consider configuring a **Recovery Signal** and recovery window.

   For example, a link-down Reactor may wait for a corresponding link-up Signal before invoking remediation:

   ```text
   Trigger:
     fields.zabbix.event_opdata starts with "Current state: down"

   Recovery:
     fields.zabbix.event_opdata starts with "Current state: up"
   ```

   Correlate the recovery using values that identify the same affected object, such as host and interface.

   This allows short-lived faults to resolve naturally without unnecessarily executing automation.

7. **Enable Automatic mode**

   Only after the Reactor has been successfully exercised in Observe mode should it normally be changed to **Automatic**.

   After enabling Automatic mode, verify the first real execution through the Signal, Reaction and Job Inspect pages to confirm the complete workflow behaves as expected.

This approach separates development into three stages:

```text
Understand the Signal
        ↓
Validate the Reactor in Observe mode
        ↓
Enable automatic Reaction execution
```

This reduces the likelihood of unexpected automation being triggered by an incorrectly understood or overly broad Signal.

