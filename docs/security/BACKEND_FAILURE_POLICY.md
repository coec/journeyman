# Backend Timeout, Retry, and Resource-Release Policy

Journeyman treats a stalled backend as a bounded failure, not as permission to
hold a web worker or scheduler worker indefinitely.

## General policy

Every network request and externally-executed helper used as part of a bounded
Journeyman operation must have a finite timeout. Journeyman does not perform
unbounded automatic retries.

For ordinary HTTP, Git, database, inventory, privileged-helper, and package
management operations, the in-request retry budget is **zero**. A failure is
returned to the caller or recorded on the relevant Job/build object. A later
explicit user action or scheduled run is a new operation, not an automatic
retry. Because these paths do not automatically retry, exponential backoff is
not applicable inside the request.

Active Directory is the deliberate exception: directory operations make at
most one attempt against each enabled directory server in configured order.
This is failover across independent endpoints, not repeated retry against the
same server. Connections are unbound in `finally` blocks. Connect and receive
timeouts are independently configurable.

## Backend boundaries

| Backend / helper | Bound | Retry budget | Release / failure behaviour |
| --- | --- | --- | --- |
| Git repository sync | `JOURNEYMAN_GIT_TIMEOUT_SECONDS`, default 300 s per command | 0 | `TimeoutExpired` becomes `GitError`; no interactive prompt; redirects disabled |
| Built-in repository Git | same Git timeout | 0 | timeout raises and aborts materialisation |
| Remote-runner repository archive Git | same Git timeout | 0 | timeout becomes `RunnerArtifactError`; temporary archive removed on failure |
| Satellite / Foreman inventory | 300 s default subprocess timeout | 0 | temporary credential inventory and redirect guard removed in `finally` |
| Zabbix API | 30 s default HTTP timeout | 0 | response context is closed; redirects rejected |
| Static inventory | 60 s default subprocess timeout | 0 | temporary inventory file removed in `finally` |
| Execution target preview | `PROJECT_RUN_PREVIEW_TIMEOUT_SECONDS`, default 30 s | 0 | temporary directory cleans itself on exit |
| Managed environment commands | 900 s default; version probes 15 s | 0 | failed build rolls back/restores prior environment where available |
| Environment proxy test | 15 s per endpoint | 0 | response context closed by `with` |
| Active Directory | configured connect + receive timeouts | one attempt per enabled server | every connection unbound in `finally`; all-server failure becomes `DirectoryUnavailableError` |
| PostgreSQL | driver `connect_timeout` for remote DB; SQLAlchemy pool checkout default 10 s | 0 in request | SQLAlchemy manages connection return; failed operations roll back |
| Nginx privileged helper | 30 s | 0 | timeout becomes `SystemSettingsApplyError` |
| System-status helper commands | 3 s | 0 | failure is reported as status data rather than blocking status collection |
| Job execution | `JOURNEYMAN_JOB_TIMEOUT_SECONDS`, default 3600 s | no transparent re-run | Job/step state records timeout/failure |

The application does not catch a backend timeout and immediately repeat the
same expensive operation. This prevents retry storms and gives operators a
clear failure boundary.

## Adding a backend

A new backend or subprocess is not complete until it has:

1. a finite connection/execution timeout;
2. a documented retry budget;
3. explicit failure translation appropriate to the caller;
4. deterministic cleanup of files, sockets, subprocesses, credentials, and
   other temporary resources;
5. tests covering timeout/failure behaviour where practical.

If automatic retries are introduced later, they must be finite, use bounded
backoff with jitter, and obey an overall operation deadline rather than
multiplying the per-attempt timeout without limit.
