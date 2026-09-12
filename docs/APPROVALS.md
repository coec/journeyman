# Project approval workflow

Journeyman can require a two-stage 4-eyes approval workflow before a Project is
used for operational Package or Schedule execution.

The workflow is controlled by a system-wide setting and is intentionally
separate from normal Project development and testing.

## System-wide control

The global **4-eyes approval** setting is disabled by default.

When it is disabled:

- Project review and management approval are not required for operational
  Package or Schedule execution.
- Review and approval controls are hidden.
- Existing review and approval history is retained.
- Existing approval state is not destroyed merely because the system-wide
  control is disabled.

When it is enabled:

- ordinary Projects require approval unless explicitly exempted;
- Package and Schedule execution is allowed only for the approved executable
  Project revision;
- manual Project execution remains available to users who already have
  development/test access;
- changing an approval-relevant Project definition invalidates the current
  approval.

The setting cannot be enabled unless Directory and Authentication is enabled.

Built-in Journeyman automation is outside the user Project approval workflow.

## Roles and workflow rights

Approval authority is Journeyman-local authorization.

The workflow uses two rights:

- **Reviewer** performs technical review.
- **Approver** performs management approval.

These are rights, not roles. A user may hold one or both rights, but a single
person cannot satisfy multiple eyes for the same approval chain.

The workflow enforces separation of duties:

- the requester cannot technically review their own submission;
- only the assigned Reviewer can decide the technical review;
- the technical Reviewer cannot also provide management approval for the same
  revision;
- the requester cannot provide their own management approval;
- only the assigned Approver can decide the management approval.

## Approval states

A Project may have one of these approval states:

| State | Meaning | Operational Package/Schedule execution |
| --- | --- | --- |
| `development` | Project is being built or has returned to development | Blocked when 4-eyes applies |
| `review_requested` | Exact Project revision is awaiting technical review | Blocked |
| `technically_approved` | Technical review passed; management approval not yet complete | Blocked |
| `approved` | Current executable revision has both required approvals | Allowed |
| `stale` | Approved/submitted executable definition changed | Blocked |

Manual development/test execution is intentionally separate from operational
approval. Existing Project development authorization still controls who may
manually execute or modify a Project.

## Immutable revisions

Requesting technical review captures an immutable Project revision.

The captured definition includes the executable Project configuration, ordered
steps, effective repository/environment/inventory/credential references and the
Git commit where available. A SHA-256 digest identifies the captured definition.

Approval decisions are bound to that exact revision. Approval is not a floating
permission on the Project name.

Internal database row identifiers are not treated as executable meaning. For
example, replacing a ProjectStep database row with an otherwise identical step
does not invalidate approval merely because the internal row ID changed.

## Technical review

An authorized Project developer submits the Project for review and selects an
enabled user with the Reviewer right.

The Reviewer sees the submitted immutable revision and may approve or reject it.

- Approve -> `technically_approved`
- Reject -> `development`
- Executable definition changes before decision -> `stale`

The historical review record is retained.

## Management approval

A technically approved revision may then be submitted to an enabled user with
the Approver right.

Management approval is explicitly linked to:

- the Project;
- the immutable Project revision; and
- the technical review on which the request relies.

The assigned Approver may approve or reject it.

- Approve -> `approved`
- Reject -> `development`
- Executable definition changes before decision -> `stale`

The technical Reviewer for the revision cannot also satisfy management approval.

## Staleness

When 4-eyes applies, an approval-relevant change invalidates the current approval
immediately.

Examples include changes to:

- Project execution settings;
- steps and dependencies;
- playbooks or scripts;
- limits, tags, extra vars and execution controls;
- repositories and repository commits;
- inventories;
- credentials;
- execution environments; and
- runner/execution choices represented in the immutable Project definition.

Existing approval records remain historical evidence. They are not rewritten or
deleted.

Operational execution also rechecks the current executable definition against
the approved revision. This is a second fail-closed control in case state was not
updated earlier.

## Per-Project approval exemption

When global 4-eyes approval is enabled, an ordinary Project may be marked
**Approval not required**.

The exemption:

- defaults to off; ordinary Projects require approval;
- requires an Automation Admin who also has the Approver right to change;
- is audited;
- bypasses review/approval gating for that Project;
- retains existing review/approval history;
- closes pending review/approval attempts as stale when the Project becomes
  exempt.

If approval is later required again, Journeyman restores `approved` only when the
current executable definition still matches a previously fully approved
revision. Otherwise the Project returns to `development`.

## Operational gating

When approval applies, final approval is required for:

- Package dispatch; and
- scheduled Project execution.

An unapproved or stale Project fails closed with a clear reason. Existing
schedules are not allowed to continue silently against an invalid approval.

Manual development/test execution is not converted into operational approval and
does not bypass Package/Schedule gating.

## Job approval provenance

New Jobs retain immutable approval provenance captured at dispatch time.

The Job record includes, where applicable:

- launch source;
- development/test versus operational execution context;
- whether global 4-eyes was enabled;
- whether Project approval was required or exempt;
- Project approval state;
- Project revision ID, sequence and digest;
- technical review identity, Reviewer and decision; and
- management approval identity, Approver and decision.

This information is shown on Job detail and remains with the Job even if the
Project or approval records later change.

Reruns preserve the original Job approval provenance because reruns execute the
saved Job definition rather than reinterpreting the current Project.

Jobs created before approval provenance was introduced do not attempt to
reconstruct approval facts that were never recorded.

## State transition summary

| Event | Result |
| --- | --- |
| Create ordinary Project | `development` |
| Request technical review | `review_requested` |
| Technical reviewer approves | `technically_approved` |
| Technical reviewer rejects | `development` |
| Management approver approves | `approved` |
| Management approver rejects | `development` |
| Approval-relevant edit during/after approval | `stale` |
| Make Project approval-exempt | `development` and approval gating bypassed |
| Require approval again with matching prior full approval | `approved` |
| Require approval again without matching prior full approval | `development` |

## Audit and evidence

Approval setting changes, Project approval-policy changes, review requests and
decisions, and management approval requests and decisions are audit logged.

Relevant regression coverage includes:

- `tests/test_four_eyes_approval_toggle.py`
- `tests/test_project_reviews.py`
- `tests/test_project_review_routes.py`
- `tests/test_project_management_approvals.py`
- `tests/test_project_management_approval_routes.py`
- `tests/test_project_operational_approval.py`
- `tests/test_project_approval_staleness.py`
- `tests/test_project_approval_exemption.py`
- `tests/test_job_approval_provenance.py`
