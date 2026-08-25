import json
import re
from copy import deepcopy


MAX_STATS_BYTES = 1024 * 1024
_RESERVED_EXTRA_VAR = "journeyman_stats"
_SAFE_KEY_PATTERN = re.compile(r"[^a-z0-9_]+")


class JobStatsError(ValueError):
    """Raised when Ansible custom statistics cannot be used safely."""


def _json_safe_copy(value):
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise JobStatsError(
            "Ansible custom statistics must contain only JSON-safe values."
        ) from exc

    if len(encoded.encode("utf-8")) > MAX_STATS_BYTES:
        raise JobStatsError(
            "Ansible custom statistics exceed the 1 MiB per-step limit."
        )

    return json.loads(encoded)


def normalise_ansible_custom_stats(payload):
    """Convert callback output into global and per-host statistics."""

    if not payload:
        return {}

    if not isinstance(payload, dict):
        raise JobStatsError("Ansible custom statistics payload is not an object.")

    custom = payload.get("custom", payload)
    if not isinstance(custom, dict):
        raise JobStatsError("Ansible custom statistics are not an object.")

    global_stats = custom.get("_run", {})
    if global_stats is None:
        global_stats = {}
    if not isinstance(global_stats, dict):
        raise JobStatsError("Global Ansible custom statistics are not an object.")

    per_host = {
        str(host): values
        for host, values in custom.items()
        if host != "_run"
    }

    if any(not isinstance(values, dict) for values in per_host.values()):
        raise JobStatsError("Per-host Ansible custom statistics are not objects.")

    result = {}
    if global_stats:
        result["data"] = global_stats
    if per_host:
        result["per_host"] = per_host

    return _json_safe_copy(result)


def stats_namespace_for_step(step, existing_keys=None):
    """Return a deterministic, Ansible-friendly namespace for a JobStep."""

    existing_keys = set(existing_keys or ())
    source = str(getattr(step, "name", "") or "").strip().lower()
    key = _SAFE_KEY_PATTERN.sub("_", source).strip("_")

    if not key:
        key = "step_{}".format(getattr(step, "position", "unknown"))

    if key[0].isdigit():
        key = "step_{}".format(key)

    if key in existing_keys:
        key = "{}_step_{}".format(
            key,
            getattr(step, "position", "unknown"),
        )

    return key


def add_step_stats(propagated_stats, step, stats):
    """Add one successful step's statistics without mutating the input."""

    merged = deepcopy(propagated_stats or {})
    if not stats:
        return merged

    key = stats_namespace_for_step(step, merged)
    values = dict(stats.get("data") or {})

    per_host = stats.get("per_host") or {}
    if per_host:
        values["_hosts"] = per_host

    merged[key] = _json_safe_copy(values)
    return merged


def build_step_extra_vars(
    base_extra_vars,
    propagated_stats,
    *,
    step_extra_vars=None,
):
    """Merge Package vars, step overrides, then prior-step statistics."""

    result = deepcopy(base_extra_vars or {})
    if not isinstance(result, dict):
        raise JobStatsError("Base execution variables are not an object.")

    if _RESERVED_EXTRA_VAR in result:
        raise JobStatsError(
            "The extra variable 'journeyman_stats' is reserved by Journeyman."
        )

    step_values = deepcopy(step_extra_vars or {})
    if not isinstance(step_values, dict):
        raise JobStatsError("Step extra variables are not an object.")
    if _RESERVED_EXTRA_VAR in step_values:
        raise JobStatsError(
            "The extra variable 'journeyman_stats' is reserved by Journeyman."
        )

    # Step-local values intentionally override Package values for this step only.
    result.update(step_values)

    if propagated_stats:
        result[_RESERVED_EXTRA_VAR] = _json_safe_copy(propagated_stats)

    return result
