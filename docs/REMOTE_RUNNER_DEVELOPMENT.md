# Remote runner development topology (Journeyman 2.0)

Journeyman runs **one remote runner per Linux host**, under the fixed
`journeyman-remote-runner.service` unit and registration file
`/etc/journeyman/remote-runner.env`. Its logical name remains independent of
the host FQDN and site-aware routing is unchanged.

For runner group routing/concurrency tests, use separate VMs (or containers
with isolated systemd, filesystems and network namespaces) rather than
installing multiple runner instance services on a single host.

The formerly supported `journeyman-remote-runner@.service` template and
`remote-runner-<name>.env` registrations are migrated through
**Manage Remote Runner → Update**. See `UPGRADE.md` for safeguards and
rollback data. A second runner on an already enrolled host is refused.

Runner execution environments remain independent and can differ between sites
and runner groups; this change concerns only the runner agent runtime.
