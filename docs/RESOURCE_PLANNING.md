# Resource Planning

This document provides general CPU and memory sizing guidance for Journeyman deployments.

The figures below are intended as practical starting points rather than hard minimums. Actual requirements depend on workload, particularly the number of concurrent automation jobs, Ansible fork counts, inventory size, repository activity, and whether the main Journeyman server also executes automation locally.

## Recommended Baseline

For a typical operational deployment:

| Role | Minimum | Recommended | Higher-load starting point |
|---|---:|---:|---:|
| Journeyman main server | 2 vCPU / 4 GB RAM | 4 vCPU / 8 GB RAM | 8 vCPU / 16 GB RAM |
| Remote runner | 2 vCPU / 4 GB RAM | 4 vCPU / 8 GB RAM | 8 vCPU / 16 GB RAM |
| PostgreSQL server, if separate | 2 vCPU / 4 GB RAM | 4 vCPU / 8 GB RAM | Workload dependent |

For most environments, **4 vCPU and 8 GB RAM for the Journeyman server and each general-purpose remote runner** is a good initial allocation.

## Journeyman Main Server

The main Journeyman server is generally not CPU intensive.

Its workload includes:

- serving the web interface;
- API and database activity;
- Signal and Reaction processing;
- scheduler activity;
- repository refreshes;
- inventory resolution;
- Server-Sent Events connections;
- dispatching execution work to runners;
- local execution where the built-in runner is used.

A **2 vCPU / 4 GB RAM** system should be sufficient for a small or lightly used installation.

A **4 vCPU / 8 GB RAM** allocation is recommended for a normal operational deployment because it provides comfortable headroom for concurrent users, inventory operations, repository activity, Signals/Reactions, and background services.

An **8 vCPU / 16 GB RAM** allocation may be appropriate where the main server handles unusually high Signal rates, large inventories, heavy repository activity, many concurrent users, or substantial local automation execution.

### Local Runner Consideration

If the Journeyman main server also executes significant automation using its built-in runner, size it as both an application server and a runner.

A main server that only coordinates and dispatches work can remain relatively small. A main server that regularly launches large Ansible jobs may require additional memory and CPU.

## Remote Runners

Remote runners typically require more execution capacity than the Journeyman web application because they launch the automation workload itself.

A runner may execute:

- `ansible-playbook`;
- Python processes;
- SSH connections;
- network collection modules;
- inventory processing;
- Jinja rendering;
- task-specific scripts and utilities.

Journeyman runner sizing should therefore be based primarily on:

1. the configured maximum number of concurrent execution steps; and
2. the resource demand of each automation workload.

A useful starting guide is:

| Maximum concurrent steps | Suggested runner sizing |
|---:|---:|
| 1 | 2-4 vCPU / 4 GB RAM |
| 2-4 | 4 vCPU / 8 GB RAM |
| 5-8 | 8 vCPU / 16 GB RAM |
| More than 8 | Benchmark the actual workload |

These are intentionally conservative guidelines rather than strict limits.

Four jobs configuring a small number of network devices may consume much less memory than four jobs gathering facts from hundreds of Linux hosts.

## CPU and Memory Characteristics

For most Journeyman runner workloads, memory is likely to become a constraint before CPU.

Ansible execution can consume memory for:

- inventory structures;
- task data;
- facts;
- Jinja rendering;
- module results;
- concurrent forks;
- multiple simultaneous `ansible-playbook` processes.

CPU usage is often bursty. Parsing, rendering, inventory handling, compression and result processing can be CPU intensive for short periods, but many automation tasks spend significant time waiting for remote hosts.

For this reason, a balanced runner such as:

```text
4 vCPU
8 GB RAM
```

is generally preferable to a CPU-heavy but memory-constrained configuration.

## Runner Crews and Horizontal Scaling

Journeyman supports multiple runners within a crew.

Where redundancy is desirable, using multiple moderately sized runners is usually preferable to relying on one large runner.

For example:

```text
Runner A: 4 vCPU / 8 GB RAM
Runner B: 4 vCPU / 8 GB RAM
```

provides approximately the same aggregate compute and memory as:

```text
Single runner: 8 vCPU / 16 GB RAM
```

while also providing:

- runner redundancy;
- failover capability;
- maintenance flexibility;
- load distribution;
- reduced impact if a runner is unavailable.

This is generally the preferred design for operational sites.

## Database Considerations

CPU and memory requirements for the database are normally modest compared with automation execution.

SQLite is suitable for smaller or lightly concurrent deployments.

PostgreSQL is recommended where Journeyman is operationally important, where multiple jobs may run concurrently, or where remote runners and Signals/Reactions are used regularly.

A separate PostgreSQL server can typically begin at:

```text
2 vCPU
4 GB RAM
```

with:

```text
4 vCPU
8 GB RAM
```

providing a comfortable general-purpose allocation.

Database sizing should be reviewed if Signal volume, audit retention, job history or concurrent activity becomes unusually high.

## Disk Capacity

Disk requirements vary much more than CPU and memory requirements.

Important consumers include:

- Git repositories;
- job output and execution history;
- Signal and Reaction history;
- audit records;
- uploaded or generated artifacts;
- logs;
- Python virtual environments;
- database growth.

Disk should therefore be planned according to retention policy, repository size and execution frequency rather than using a fixed Journeyman-specific figure.

## Practical Deployment Examples

### Small or Test Environment

```text
Journeyman:
  2 vCPU
  4 GB RAM

Runner:
  built-in runner or
  2 vCPU
  4 GB RAM
```

Suitable for development, testing, demonstrations, and low-concurrency environments.

### Normal Operational Deployment

```text
Journeyman:
  4 vCPU
  8 GB RAM

Remote runner:
  4 vCPU
  8 GB RAM

PostgreSQL:
  local or separate
  2-4 vCPU
  4-8 GB RAM
```

This is the recommended general starting point.

### Redundant Site Execution

```text
Journeyman:
  4 vCPU
  8 GB RAM

Runner crew:
  Runner A: 4 vCPU / 8 GB RAM
  Runner B: 4 vCPU / 8 GB RAM
```

This provides both execution capacity and runner redundancy.

## Capacity Monitoring

Resource allocations should be reviewed after deployment using real workload data.

Useful runner metrics include:

- CPU utilisation;
- memory utilisation;
- load average;
- swap activity;
- concurrent execution count;
- Ansible execution duration;
- queue wait time.

Increase runner capacity or add additional crew members if sustained resource pressure or queueing becomes visible.

For the main Journeyman server, monitor:

- CPU utilisation;
- memory utilisation;
- database latency;
- web response time;
- Signal processing rate;
- repository and inventory refresh duration.

## Summary

A sensible default deployment is:

```text
Journeyman main server:
  4 vCPU
  8 GB RAM

Remote runner:
  4 vCPU
  8 GB RAM
```

Start smaller for development or lightly used systems, and scale runners according to execution concurrency and actual Ansible workload.

Where redundancy is required, prefer multiple moderate-sized runners in a crew rather than a single oversized runner.

