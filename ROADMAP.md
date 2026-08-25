# Journeyman Roadmap

## Release status

Current: v1.5.0
Target: v1.6.0

## v1.6.0

- add a system status/notices facility for operational information such as
  pip-audit findings on remote runners and counts of dispatched, queued and
  executing jobs
- rerun a failed project from the failed step
- optionally force all host names in an inventory to lower case
- SMIT enhancements (add "View as Ansible" facility on inventories,
  projects and packages).
- prevent a runner from being updated while it is running projects/packages
  by implementing draining

## v1.7.0

- enhance collection documentation to include more diverse examples
- review all pytests for duplication and relevance

## v2.0.0

- Redesign encryption key handling to use X.509 keys and certificates

## Future

- systematic BOLA/IDOR authorization regression testing
- deeper release validation
- additional execution/task backends
- operational lifecycle work
- configuration portability/Git-managed definitions
- allow runners to "hand-over" execution to another runner in their crew
  to facilitate runner maintenance in environments where projects/packages
  are running 24/7

