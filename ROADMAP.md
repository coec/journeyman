# Journeyman Roadmap

## Release status

Current: v1.8.0
Target: v1.9.0

## v1.9.0

- Allow multistep projects with remote scripts and playbooks, currently
  multistep projects must be all script or all playbook

## v2.0.0

- Redesign encryption key handling to use X.509 keys and certificates
- Local authorization model with separate administration roles and Reviewer /
  Approver workflow rights
- Optional system-wide 4-eyes Project approval with immutable revisions,
  technical review, management approval, staleness enforcement, per-Project
  exemption, operational Package/Schedule gating, and Job approval provenance

## Future

- systematic BOLA/IDOR authorization regression testing
- deeper release validation
- additional execution/task backends
- operational lifecycle work
- configuration portability/Git-managed definitions
- allow runners to "hand-over" execution to another runner in their crew
  to facilitate runner maintenance in environments where projects/packages
  are running 24/7

