# Journeyman Reactions — Zabbix 7.4 setup

This installs the **Journeyman Reactions** Zabbix Webhook Media Type used by
Journeyman's:

    Source → Signal → Reactor → Reaction

workflow.

The media type is imported **disabled** so that it cannot begin sending Signals
before its Source UUID and HMAC secret have been configured.

## 1. Create the Source in Journeyman

In Journeyman:

1. Open **Sources**.
2. Create a Source of type **Zabbix**.
3. Set the Zabbix URL for identification/documentation.
4. Add the IP address/CIDR from which the Zabbix webhook reaches Journeyman.
5. Save it.
6. Copy:
   - the **Source UUID**
   - the **HMAC secret** shown at creation time

The secret is shown only when created or regenerated.

## 2. Import the media type into Zabbix

In Zabbix 7.4:

1. Open **Alerts → Media types**.
2. Click **Import**.
3. Import `media_journeyman_reactions.yaml`.
4. Leave it disabled until the macros below are configured.

## 3. Create three global Zabbix macros

Open **Administration → Macros** and create:

| Macro | Type | Example |
|---|---|---|
| `{$JOURNEYMAN.URL}` | Text | `https://journeyman.example` |
| `{$JOURNEYMAN.SOURCE.UUID}` | Text | UUID copied from the Journeyman Source |
| `{$JOURNEYMAN.HMAC.SECRET}` | **Secret text** | Secret copied from the Journeyman Source |

`{$JOURNEYMAN.URL}` must be the HTTPS base URL only; do not append
`/api/signals/zabbix`.

Using a **Secret text** macro means the HMAC secret is not embedded in the
import/export file or displayed as ordinary clear text in Zabbix.

## 4. Enable and test the media type

Open **Alerts → Media types → Journeyman Reactions**.

Enable the media type.

Use **Test** to verify basic connectivity. Note that Zabbix's media-type test
dialog may not provide a real trigger context for all `{EVENT.*}` and
`{HOST.*}` macros, so the definitive test is a real Action notification.

Journeyman accepts HTTP 202 for a new Signal and HTTP 200 when the same
Source/Signal has already been accepted.

## 5. Add a dedicated Zabbix recipient

A Zabbix Media Type is delivered through a user/media recipient.

Create or use a dedicated integration user, for example:

    Journeyman Reactor

Add media:

    Type: Journeyman Reactions
    Send to: journeyman
    When active: 1-7,00:00-24:00
    Use if severity: all desired severities
    Enabled: yes

The **Send to** value is not used by the webhook; `journeyman` is merely a
placeholder required by Zabbix.

Ensure the recipient has the host permissions required for the Action
notifications you want forwarded.

## 6. Create a Zabbix Action

For a broad commissioning test, create a **Trigger action** such as:

    Name: Journeyman Signals

Conditions:
    choose the host groups/tags/severities you actually want Journeyman to see

Operations:
    Send message to: Journeyman Reactor
    Send only to: Journeyman Reactions

For initial commissioning, keep the Journeyman Reactor itself in **Observe**
mode.

You can start narrowly — for example, only the recurring
`ICMP Ping: Unavailable by ICMP ping` problem — and broaden the Action later.

## Signal produced

A Zabbix problem is normalized approximately as:

```json
{
  "schema_version": 1,
  "signal_id": "938274:1",
  "signal_type": "problem",
  "timestamp": "2026-08-12T07:14:22.000Z",
  "host": "dbdyder003",
  "severity": "Average",
  "description": "ICMP Ping: Unavailable by ICMP ping",
  "fields": {
    "tags": {
      "tablespace": "USERS"
    },
    "zabbix": {
      "event_id": "938274",
      "event_value": "1",
      "event_status": "PROBLEM",
      "event_nseverity": "3",
      "event_opdata": "",
      "host_id": "12345",
      "host_name": "dbdyder003",
      "trigger_id": "67890"
    }
  }
}
```

For the Package input you were testing:

    device_name ← Signal field `host`

For a tablespace Reactor, put the tablespace name into a Zabbix trigger/event
tag, for example:

    tablespace = USERS

The webhook turns `{EVENT.TAGSJSON}` into `fields.tags`, so the mapping becomes:

    tablespace ← Signal field `fields.tags.tablespace`

This is much preferable to parsing the human-readable problem description.

## Authentication performed by the webhook

For every POST the media type calculates:

    HMAC-SHA256(
        source_secret,
        source_uuid + "\n" +
        request_unix_timestamp + "\n" +
        exact_raw_json_body
    )

and sends:

    X-Journeyman-Source
    X-Journeyman-Timestamp
    X-Journeyman-Signature

The request is sent only to HTTPS URLs. Journeyman additionally checks the
configured sender IP/CIDR and rejects stale timestamps or invalid signatures.

## Zabbix tags are Reactor parameters

Any Zabbix event tag is forwarded as a structured Signal field.

For example, trigger tags:

    tablespace = USERS
    database   = POMPROD

become:

    fields.tags.tablespace = USERS
    fields.tags.database   = POMPROD

and can be mapped directly to Reaction Package inputs.

