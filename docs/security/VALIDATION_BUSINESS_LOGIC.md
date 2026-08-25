# Validation and Business Logic Security

This document records Journeyman's validation and business-logic rules for the OWASP ASVS 5.0.0 V2 assessment.

## Validation boundaries

All browser, API, Package and inventory-derived values are untrusted until validated by the server-side code that owns the operation. Browser controls are usability features, not security controls. Typed Package inputs are validated by `app/services/project_package_inputs.py`; choice values must be members of the configured server-side choice set, integer values are range checked, required and conditional fields are re-evaluated on the server, email-address inputs are parsed and validated item-by-item, and YAML configuration is parsed with safe loaders and checked for the expected list/mapping/scalar structure.

Fields with narrower semantics apply narrower validation. Examples include absolute Ansible configuration paths, bounded Project parallelism, restricted inventory-binding expressions, runner identifiers/tokens, and repository/playbook paths constrained to trusted repositories.

## Related-data consistency

Journeyman validates combinations of related fields rather than accepting each field independently. Package `visible_when` and `required_when` conditions are evaluated server-side; values for hidden inputs are ignored rather than trusted; an input which becomes required through a condition must be present. Package preview/confirmation binds the user, Package, validated values and definition digest so a confirmation cannot be reused after changing the Package definition or by a different user.

## Business-logic limits

Current explicit limits include:

- Project parallel step count is restricted to 1 through 32 and the execution service applies the same bound.
- Package choice, integer, text/password length, required-field and condition rules are enforced at the trusted service layer.
- Runner job claiming is atomic and a queued unit cannot be claimed twice.
- Runner Crew selection accounts for already assigned work when choosing capacity.
- Package execution requires the expected preview/confirmation sequence when confirmation is configured.

Journeyman does not currently impose a general per-user launch rate limit or a global rate limit on all expensive application operations. These anti-automation controls remain a deferred security item.

## Transactions and rollback

Database-backed multi-object changes use SQLAlchemy transactions and explicitly roll back on failure in the relevant create/edit/execution services. Inventory snapshot creation also removes filesystem snapshots created by a failed database transaction. This provides transaction boundaries for the application's persistence operations; it does not make external Ansible, shell, Git, Satellite or other remote side effects transactional. Operational workflows must use application-specific rollback/failure steps where required.

## Scarce-resource booking

Journeyman does not implement user-reservable scarce business resources such as seats, stock units, appointments or monetary balances, so ASVS limited-quantity double-booking requirements are not applicable to the current product. Runner capacity is scheduling capacity rather than a user-owned reservable resource and is protected separately by atomic dispatch/claim logic.

## High-value approvals

Journeyman can execute operationally high-impact automation. It currently supports warnings, confirmation, permissions and audit logging, but does not implement mandatory two-person/four-eyes approval for high-value flows. This remains deferred rather than being represented as satisfied by a single-user confirmation dialog.

## Anti-automation

Journeyman is itself an automation product and intentionally supports scheduled and rapid machine-driven execution. A requirement for "human-speed" submission is therefore not applicable. Controls against abusive or excessive use, however, are applicable and remain deferred until general per-user/global rate or quota controls exist for costly functions.
