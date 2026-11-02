"""Unit tests for Panopti Swarm Topology Mesh and Causal Attribution."""

from __future__ import annotations

from panopti.mesh import SwarmMesh


def test_swarm_mesh_registration_and_hierarchy() -> None:
    """Agents can be registered with parent-child relationships."""
    mesh = SwarmMesh(root_agent_id="lead_planner", root_role="orchestrator")
    mesh.register_agent(
        "coder", role="worker", parent_id="lead_planner", task_description="Write tests"
    )
    mesh.register_agent(
        "reviewer", role="auditor", parent_id="lead_planner", task_description="Audit security"
    )

    root = mesh._nodes["lead_planner"]
    assert "coder" in root.children
    assert "reviewer" in root.children
    assert mesh._nodes["coder"].parent_id == "lead_planner"


def test_swarm_mesh_root_cause_attribution() -> None:
    """Failure attribution traces backward to the root ancestor."""
    mesh = SwarmMesh(root_agent_id="supervisor")
    mesh.register_agent("worker_a", parent_id="supervisor")
    mesh.register_agent("sub_tool_b", parent_id="worker_a")

    mesh.record_outcome("sub_tool_b", success=False, reason="Null pointer in JSON parse")

    lineage = mesh.attribute_root_cause("sub_tool_b")
    assert len(lineage) == 3
    assert [n.agent_id for n in lineage] == ["supervisor", "worker_a", "sub_tool_b"]
    assert lineage[-1].failure_reason == "Null pointer in JSON parse"


def test_swarm_mesh_ascii_dag_rendering() -> None:
    """Mesh outputs non-empty ASCII representation of the tree."""
    mesh = SwarmMesh(root_agent_id="director")
    mesh.register_agent("scout", parent_id="director")
    mesh.record_outcome("scout", success=True)

    dag = mesh.render_ascii_dag()
    assert "director" in dag
    assert "scout" in dag
    assert "completed" in dag
