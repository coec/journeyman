"""Build a read-only flowchart specification from a Project workflow."""


def build_project_flowchart(project):
    """Return JSON-serialisable nodes and conditional dependency edges."""

    steps = sorted(
        project.steps,
        key=lambda step: step.position,
    )

    nodes = []

    for step in steps:
        effective_inventory = (
            step.inventory
            or project.inventory
        )

        nodes.append(
            {
                "position": step.position,
                "name": step.name,
                "artifact": step.playbook,
                "enabled": bool(step.enabled),
                "failure_only": bool(
                    getattr(
                        step,
                        "failure_only",
                        False,
                    )
                ),
                "refresh_inventory_after": bool(
                    getattr(
                        step,
                        "refresh_inventory_after",
                        False,
                    )
                ),
                "inventory": (
                    effective_inventory.name
                    if effective_inventory is not None
                    else ""
                ),
                "inventory_override": bool(
                    step.inventory_id
                ),
            }
        )

    positions = {
        node["position"]
        for node in nodes
    }

    edges = []

    for step in steps:
        condition = (
            "failure"
            if getattr(
                step,
                "failure_only",
                False,
            )
            else "success"
        )

        for dependency_position in (
            step.get_dependency_positions()
        ):
            if dependency_position not in positions:
                continue

            edges.append(
                {
                    "from": dependency_position,
                    "to": step.position,
                    "condition": condition,
                }
            )

    return {
        "project_id": project.id,
        "project_name": project.name,
        "execution_type": (
            project.execution_type
            or "ansible"
        ),
        "nodes": nodes,
        "edges": edges,
    }
