from types import SimpleNamespace

import pytest

from app.models import JobStep
from app.services.job_stats import (
    JobStatsError,
    add_step_stats,
    build_step_extra_vars,
    normalise_ansible_custom_stats,
    stats_namespace_for_step,
)


def _step(position=1, name="Provision VM"):
    return SimpleNamespace(position=position, name=name)


def test_normalise_global_and_per_host_stats():
    result = normalise_ansible_custom_stats(
        {
            "custom": {
                "_run": {
                    "hostname": "host42",
                    "ports": [80, 443],
                },
                "host-a": {
                    "serial": "ABC123",
                },
            }
        }
    )

    assert result == {
        "data": {
            "hostname": "host42",
            "ports": [80, 443],
        },
        "per_host": {
            "host-a": {
                "serial": "ABC123",
            }
        },
    }


def test_stats_propagate_under_step_name_namespace():
    propagated = add_step_stats(
        {},
        _step(),
        {
            "data": {
                "hostname": "host42",
            },
            "per_host": {
                "host-a": {
                    "serial": "ABC123",
                }
            },
        },
    )

    assert propagated == {
        "provision_vm": {
            "hostname": "host42",
            "_hosts": {
                "host-a": {
                    "serial": "ABC123",
                }
            },
        }
    }

    extra_vars = build_step_extra_vars(
        {"package_value": "locked"},
        propagated,
    )

    assert extra_vars["package_value"] == "locked"
    assert (
        extra_vars["journeyman_stats"]["provision_vm"]["hostname"]
        == "host42"
    )


def test_duplicate_step_names_receive_stable_suffix():
    assert stats_namespace_for_step(_step(1, "Configure"), set()) == "configure"
    assert (
        stats_namespace_for_step(_step(2, "Configure"), {"configure"})
        == "configure_step_2"
    )


def test_reserved_package_variable_is_rejected():
    with pytest.raises(JobStatsError, match="reserved"):
        build_step_extra_vars(
            {"journeyman_stats": {"forged": True}},
            {},
        )


def test_non_json_stats_are_rejected():
    with pytest.raises(JobStatsError, match="JSON-safe"):
        normalise_ansible_custom_stats(
            {
                "custom": {
                    "_run": {
                        "invalid": object(),
                    }
                }
            }
        )


def test_job_step_custom_stats_round_trip():
    step = JobStep(
        position=1,
        name="Publish values",
        playbook="publish.yml",
        status="pending",
    )
    step.set_custom_stats(
        {
            "data": {
                "hostname": "host42",
            }
        }
    )

    assert step.get_custom_stats() == {
        "data": {
            "hostname": "host42",
        }
    }


def test_step_extra_vars_override_package_values_for_only_that_step():
    extra_vars = build_step_extra_vars(
        {"my_index": 99, "package_value": "kept"},
        {},
        step_extra_vars={"my_index": 0},
    )

    assert extra_vars == {
        "my_index": 0,
        "package_value": "kept",
    }

    second_step = build_step_extra_vars(
        {"my_index": 99, "package_value": "kept"},
        {},
        step_extra_vars={"my_index": 1},
    )
    assert second_step["my_index"] == 1


def test_reserved_step_variable_is_rejected():
    with pytest.raises(JobStatsError, match="reserved"):
        build_step_extra_vars(
            {},
            {},
            step_extra_vars={"journeyman_stats": {"forged": True}},
        )
