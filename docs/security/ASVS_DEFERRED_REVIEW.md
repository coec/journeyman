# ASVS Deferred-Control Review

Status: initial release triage completed.

`ASVS_DEFERRED.csv` is the release backlog for every requirement whose matrix
status is `Deferred`. It does not change ASVS verification status.


## Current triage summary

The automated v1 closeout pass leaves 46 Deferred requirements:

- **Pre-release required:** 6
- **Pre-release desirable:** 17
- **Accepted post-release:** 23

The current counts are reported dynamically by `python scripts/asvs_matrix.py --check`
and are not security-verification statuses in their own right.

## Dispositions

- **Pre-release required** — blocks public v1.0 unless implemented and verified,
  or an explicit documented release-risk exception is approved.
- **Pre-release desirable** — should be completed before v1.0 where practical;
  may be accepted with documented mitigation and review.
- **Accepted post-release** — consciously retained backlog, generally Level-3,
  infrastructure-dependent, or disproportionate for v1.0. It is not an ASVS pass.

Whenever a matrix row changes to or from `Deferred`, update
`ASVS_DEFERRED.csv` in the same change. `scripts/asvs_matrix.py --check`
validates that both files contain exactly the same Deferred requirement IDs.

This triage does not replace independent penetration testing, deployment review,
threat-model review, or formal release security sign-off.
