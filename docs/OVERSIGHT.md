# Project Oversight

Journeyman can pause a multi-step Project between workflow execution batches so a reviewer can inspect the resolved next step or steps before execution continues.

Oversight is intended for two related purposes:

- operator governance before later workflow steps are allowed to run; and
- safer development and validation of new multi-step Projects.

## Enabling Oversight

On a Project, enable:

```text
Oversight required between all steps
```

The Project default is **false**.

When enabled, the first dependency-free workflow step or parallel root batch is allowed to start normally. After that batch completes, Journeyman pauses before any newly runnable downstream steps are dispatched.

For execution safety, an Oversight-enabled Project is dispatched using Journeyman execution slices even when all work is destined for one local or remote runner. This keeps control with the Journeyman server between workflow steps.

## Reviewer

The initial implementation snapshots the Project or Package launcher as the reviewer for the Job. Administrators may also perform the review.

A future extension may allow another reviewer to be selected at dispatch time. Notification targets such as email are also expected to complement Oversight so a reviewer does not need to watch the Jobs page continuously.

Automatically dispatched Reactions cannot use Projects requiring Oversight because there is no interactive reviewer for an automatic Reaction.

## Oversight page

When Journeyman reaches a boundary requiring review, the Job status becomes:

```text
Waiting Oversight
```

The Oversight page shows the actual next runnable step or steps. For each step it includes:

- step name and automation artifact;
- dependency positions;
- resolved inventory name and host count;
- an expandable list of the resolved target hosts;
- immutable repository commit used by the Job;
- commit message, author and timestamp captured when the Job was queued;
- execution environment;
- planned local/remote execution destinations; and
- credential names and types, never credential secrets.

The reviewer can choose:

- **Continue** — approve the currently runnable batch; or
- **Stop Project** — cancel remaining workflow work.

Continuing only authorises the currently displayed next batch. Later workflow boundaries require Oversight again.

## Branching workflows

Oversight follows the resolved workflow rather than assuming a linear sequence.

If one completed step makes several downstream steps runnable, all of those steps are presented together on the Oversight page. Failure-only branches are presented only when their failure condition is actually selected.

This is particularly useful while developing workflows because the operator can validate the resolved branch, inventory, repository version and execution destination before allowing the next automation to touch hosts.

## Immutable execution state

Oversight does not edit dispatch inputs or change an already queued Job. Continue/Stop are control decisions over immutable execution snapshots.

Repository and inventory information displayed during review comes from the Job snapshots, not from whatever the live Project, Repository or Inventory happens to contain later.


## Selective oversight boundaries

Oversight is configured on workflow boundaries. Each non-terminal Project step can enable **Oversight after this step**. Journeyman then pauses after that step finishes and before any dependent next step becomes executable.

The Project form also provides **Oversight required between all steps** as a tri-state convenience control:

- checked: every applicable inter-step boundary requires oversight;
- unchecked: no boundaries require oversight;
- grey/indeterminate: only selected boundaries require oversight.

The first/root step does not require oversight before it starts. Terminal steps do not expose an oversight control because there is no following work to review. At a branch point, one oversight boundary can review all dependent next steps together.
