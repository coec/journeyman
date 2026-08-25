# Journeyman authorization model

This document records the authorization rules used by Journeyman and is evidence for the OWASP ASVS 5.0.0 Authorization assessment. Authentication establishes an identity; authorization is always enforced by Journeyman server-side and must not depend on whether the browser renders a button or navigation item.

## Roles and subjects

Journeyman currently distinguishes authenticated users from administrators. Directory-backed Package permissions can additionally name individual users and groups. Remote runners are service identities, not interactive users; they authenticate independently and are only permitted to retrieve or update work assigned to them.

## Function-level rules

Administrative configuration functions are restricted to administrators. This includes system and directory settings, audit-log administration, execution environments, repositories, inventories, Projects, Package administration, schedules, runner registration/management, and Runner Crews where the corresponding route is administrative.

Packages are the normal end-user dispatch surface. A Package can be available to all authenticated users or restricted to explicitly authorised users/groups. Disabled Packages and disabled Projects cannot be dispatched even by an administrator.

Authorization is enforced by route/service code. Hiding an action in HTML is usability only and is never considered an authorization control.

## Object/data-level rules

Jobs are owned by the identity that requested them. An ordinary user may view job details, live status, step/slice output, and request cancellation only for their own Jobs. Administrators may inspect and control Jobs across users. Direct requests using another Job ID are subject to the same check, preventing IDOR/BOLA access.

Security-scoped objects use owner/scope rules. Private objects are usable by their owner and administrators; public objects may be used by authenticated users; shared currently behaves like private until explicit generic sharing is implemented. Management rights remain with the owner or administrator and are not implied by public/use access.

Package permissions grant dispatch/use permission, not Package administration permission.

## Field-level rules

Secret fields are treated more restrictively than the containing object. In particular, an existing Credential secret can only be revealed by the Credential owner; administrative status alone does not grant secret-reveal permission. Job and Package views must not render stored secret values merely because the surrounding object is visible.

This distinction is intentional: object administration/use permission and access to sensitive fields are separate authorization decisions.

## Remote runner delegation

A remote runner does not inherit broad permissions from the Journeyman service account. Journeyman authorizes the originating interactive operation before queueing work and snapshots the requesting identity on the Job. Runner APIs then enforce runner identity, assignment and dispatch-token checks so a runner cannot select arbitrary Jobs or retrieve another runner's execution material.

Runner delegation does not currently re-evaluate the originating user's directory authorization after a Job has been queued. This limitation is tracked under ASVS v5.0.0-8.3.2.

## Contextual/adaptive authorization

Journeyman does not currently use time of day, source IP address, geolocation, device posture, behavioural risk scores, or similar environmental/contextual attributes to grant or deny access. Authorization is based on authenticated identity, administrator role, ownership, Package permissions, object state, and runner assignment.

Adaptive/continuous authorization is therefore not part of the current authorization model. The ASVS Level 3 administrative-interface requirement for continuous identity/device/context risk controls is explicitly deferred rather than claimed as implemented.

## Change propagation

Changes to Journeyman-owned authorization data such as Package permission rows are read from the database during subsequent operations and therefore take effect without modifying client-side state. However, directory-derived role/group identity is established by the authentication/session flow and is not currently guaranteed to be revalidated immediately after an external directory membership change. Immediate revocation across an already-authenticated session is therefore not claimed.

## Multi-tenancy

Journeyman is not currently a multi-tenant SaaS application and has no tenant boundary or tenant identifier. User/object authorization boundaries still apply, but ASVS cross-tenant isolation requirements are not applicable to the current architecture.
