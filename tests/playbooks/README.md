# Journeyman operational release-validation playbooks

These playbooks complement the pytest suite. Pytest verifies application code; this
suite verifies that an installed Journeyman instance can perform harmless Linux
automation through the same Inventory → Credential → Job → Runner → Ansible path
used by normal Projects.

The mandatory suite intentionally requires only Linux/UNIX SSH targets. Cisco,
VMware, RHV, Satellite and other product-specific systems are not assumed to exist
at every Journeyman installation.

## Configuration

Administrators configure the suite at **System Settings → Release Testing**:

- an enabled Inventory containing non-production Linux nodes;
- an Ansible host pattern selecting the test nodes;
- a **Machine (Linux/UNIX)** Credential;
- optional alternate become users, one per line; and
- optionally a Runner Crew. Leaving Runner Crew blank uses the built-in local runner.

System Settings stores references to existing Inventory, Credential and Runner Crew
objects. Credential secrets are never copied into release-test settings.

Saving valid settings creates or refreshes the admin-only built-in Package
`ZZ - Journeyman Release Validation`. **Run Validation Suite** opens its normal
Package launch page so the operator can review the host pattern before dispatch.
The resulting execution is an ordinary Journeyman Job and therefore exercises the
normal snapshots, runner dispatch and output/status handling.

## Included playbooks

`release_linux_validation.yml` is the aggregate entry point and imports:

- `linux_connectivity.yml` — proves SSH login and verifies the effective login user;
- `linux_become.yml` — proves the Machine Credential's default become user and each
  configured task-level alternate `become_user`; and
- `linux_runtime_variables.yml` — proves fixed runtime data reaches Ansible unchanged.

The remote commands use `ansible.builtin.raw` where practical so old Linux systems
without a Python version supported by the selected ansible-core can still validate
SSH and privilege escalation. Assertions execute on the Ansible controller.

## Safety

Use only disposable or explicitly approved non-production hosts. The shipped suite
runs identity commands (`id -un`) and privilege escalation; it does not write files,
install packages, restart services or otherwise intentionally alter target state.

The host pattern is deliberately shown again on the Package launch page. Treat that
review as a release-validation safety check rather than hiding it behind one-click
execution.

## Expected result

A successful run proves, for every selected host:

1. the configured Machine Credential can authenticate;
2. the effective login user matches the Credential username;
3. default privilege escalation reaches the Credential's configured become user;
4. each configured alternate `become_user` overrides the default correctly; and
5. runtime variables are preserved through the execution pipeline.

Additional orchestration tests (deliberate failure, cancellation, multi-slice failure,
mid-workflow inventory refresh and rerun behaviour) should be added separately. They
need result-state assertions in Journeyman as well as an Ansible playbook, so they are
not part of this first harmless Linux suite.

## Multi-host slice and expected-failure validation

The normal validation suite should be run against at least two hosts when possible.
With the built-in local runner, all locally routed hosts for one step belong to one
**multi-host execution slice**. Journeyman only creates multiple slices when a step's
hosts are routed to different runners, so an installation with no remote runners
cannot meaningfully test cross-runner multi-slice fan-out.

`release_linux_partial_failure.yml` backs the separate built-in Package
`ZZ - Journeyman Release Failure Validation`. It is an **expected failure** test:

1. all selected hosts first prove SSH login with the configured Machine Credential;
2. one exact `inventory_hostname`, supplied at launch, fails with the marker
   `JOURNEYMAN_EXPECTED_PARTIAL_FAILURE`;
3. all other selected hosts continue and prove their login identity again; and
4. Journeyman is expected to propagate the non-zero Ansible result to failed Slice,
   Step and Job state.

The Release Testing settings page evaluates the most recent run. A failed Job is a
**PASS** for this test only when the deliberate-failure marker is present and the
Job, Step and at least one execution Slice all reached `failed`. If the nominated
host does not match, the playbook succeeds and the expected-failure validation is
reported as failed.

This test intentionally changes no remote state. Its Job is nevertheless shown as
FAILED in the normal Jobs UI because that is precisely the propagation behaviour
being validated.
