"""Runner Crew selection and health helpers."""

from app.services.runners import runner_health
from app.services.runner_environments import runner_environment_ready


class RunnerCrewSelectionError(RuntimeError):
    """Raised when a Runner Crew has no eligible member for work."""


def normalized_load(runner, minutes=1):
    value = runner.load_average_1m if minutes == 1 else runner.load_average_5m
    if value is None or not runner.cpu_count:
        return None
    try:
        cpus = max(1, int(runner.cpu_count))
        return max(0.0, float(value)) / cpus
    except (TypeError, ValueError):
        return None


def eligible_crew_runners(crew, required_capabilities=None, required_environment=None):
    required = {
        str(item).strip().lower()
        for item in (required_capabilities or [])
        if str(item).strip()
    }
    if crew is None or not crew.enabled:
        return []

    eligible = []
    for runner in crew.runners:
        if runner.is_local or not runner.enabled or not runner.is_registered:
            continue
        if runner_health(runner) != "healthy":
            continue
        if runner.running_steps >= runner.max_concurrent_steps:
            continue
        if not required.issubset(runner.capabilities()):
            continue
        if required_environment is not None and not runner_environment_ready(
            runner, required_environment
        ):
            continue
        eligible.append(runner)
    return eligible


def select_crew_runner(
    crew,
    *,
    required_capabilities=None,
    required_environment=None,
    additional_loads=None,
):
    """Return the least-busy eligible member of ``crew``.

    Active Journeyman work is the primary score.  Normalized 1-minute and then
    5-minute system load are tie-breakers.  Unknown load metrics sort after
    known metrics but do not make an otherwise eligible runner unavailable.
    ``additional_loads`` lets one queueing transaction account for work it has
    already planned but not yet visible in runner heartbeats.
    """

    eligible = eligible_crew_runners(
        crew,
        required_capabilities,
        required_environment,
    )
    if not eligible:
        required = ", ".join(sorted(required_capabilities or [])) or "required capabilities"
        if required_environment is not None:
            required = '{} and execution environment "{}" ready'.format(
                required, required_environment.name
            )
        raise RunnerCrewSelectionError(
            'Runner Crew "{}" has no healthy available runners satisfying {}.'
            .format(crew.name if crew is not None else "", required)
        )

    additional_loads = additional_loads or {}

    def score(runner):
        one = normalized_load(runner, 1)
        five = normalized_load(runner, 5)
        return (
            int(runner.running_steps or 0) + int(additional_loads.get(runner.id, 0)),
            1 if one is None else 0,
            one if one is not None else 0.0,
            1 if five is None else 0,
            five if five is not None else 0.0,
            str(runner.name or "").lower(),
            runner.id,
        )

    return min(eligible, key=score)
