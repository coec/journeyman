# Notifications

Journeyman notifications use four deliberately separate concepts:

- **Notification Target** — a reusable destination under **Resources**. Phase 1 supports Email, Webhook and Syslog targets.
- **Notification Rule** — a subscription on a Package, Project, Project step or Reactor.
- **Notification Event** — the canonical lifecycle event produced once by execution, for example `execution.failed`, `step.failed` or `oversight.required`.
- **Notification Delivery** — one attempt to deliver one event to one resolved target.

This separation prevents wrapper objects from producing duplicate messages. If a Package and its Project both subscribe `execution.failed` to the same target, Journeyman resolves both rules against the same canonical event and creates only one delivery for that target.

## Notification Targets

Administrators manage targets under **Resources → Notification Targets**.

### Email

Email targets contain the SMTP server, transport mode, optional SMTP username/password, sender and recipient addresses. SMTP passwords are encrypted with Journeyman's credential key. STARTTLS and implicit TLS are supported, and TLS may also be set to **None** for trusted internal relays where transport encryption is not required.

### Webhook

Webhook targets POST a small JSON document containing the canonical event name, message, Job ID and optional step ID. An optional bearer token is encrypted at rest. Production outbound policy applies, including `JOURNEYMAN_OUTBOUND_ALLOWED_HOSTS` and HTTPS enforcement.

### Syslog

Syslog targets send one RFC-style severity/informational message over UDP or TCP. The destination is subject to Journeyman's outbound host allowlist.

## Testing targets

An administrator can use **Actions → Test** on any Notification Target, including a disabled target. Testing is explicit and does not create a normal lifecycle Notification Event or Delivery. Journeyman attempts delivery immediately and reports a sanitised success or failure in the web interface. Test sends are audited as `notification.test_sent` or `notification.test_failed`.

The synthetic event name used by webhook tests is `notification.test`. Email and Syslog tests are clearly labelled as Journeyman test notifications.

## Rules

Saved Projects expose a **Notifications** action. The page can add rules at either Project or individual Project-step scope.

Saved Packages and Reactors also expose **Notifications** actions.

Project and Package events:

- `execution.started`
- `execution.succeeded`
- `execution.failed`
- `execution.cancelled`
- `oversight.required`

Project-step events:

- `step.started`
- `step.succeeded`
- `step.failed`
- `step.cancelled`

Reactor rules subscribe to the same canonical execution events as the Package/Project used by an automatic Reaction. This means a Reactor and its Package may both subscribe to `execution.failed` without producing duplicate deliveries when they use the same target.

## Oversight

`oversight.required` is queued each time an execution reaches a new Oversight boundary. The message includes the assigned reviewer and a direct link to the Job's Oversight page.

Notification delivery does not block workflow execution. Events are written transactionally with Job state and the existing Journeyman scheduler service resolves and delivers them. Failed deliveries are retried up to three times and are audited as `notification.failed`; successful deliveries are audited as `notification.sent`.

## Noise control

Journeyman has no notification rules by default. Reaction notifications therefore remain silent unless an administrator explicitly adds Reactor, Package or Project rules. This is intentional: automatic Reactions can be high-volume and should not become chatty merely because notification support is enabled.

### Event-time snapshots

Notification Events capture the user-visible execution state when the event is queued.
Delivery may occur later, but messages for events such as `execution.started`,
`oversight.required`, and `step.failed` describe the state at the time of that event
rather than the Job's eventual state. This also keeps delayed Oversight notifications
from being rendered as if the Project had already succeeded.
