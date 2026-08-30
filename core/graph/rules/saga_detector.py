"""
core/graph/rules/saga_detector.py
-----------------------------------------------------------------------------
Saga Orchestrator Pattern Detector.

The Saga pattern is a legitimate distributed transaction strategy where:
  - A coordinator service calls multiple downstream services in sequence
  - Each step has a compensating transaction (rollback equivalent)
  - The overall flow is intentional and designed for consistency

WITHOUT THIS DETECTOR:
  OmniGraph's race_condition.py would flag every step of a Saga as a
  "Distributed Race Condition" because multiple services write to the same
  DB through orchestration. This is a FALSE POSITIVE.

HOW WE DETECT A SAGA COORDINATOR:
  A service is treated as a Saga Coordinator if it:
    1. Has outgoing CALLS edges to >= 2 other services
    2. Those downstream services each have WRITES_TO edges
    3. The coordinator itself does NOT write to the same targets
       (it delegates, not races)

WHAT THIS MODULE DOES:
  - Identifies Saga coordinator services
  - Marks their downstream writes as ORCHESTRATED_WRITE type
  - Returns a suppression list: findings that should be reclassified
    as INFO rather than CRITICAL

NOTE: We do NOT check for SAGA_COMPENSATION_MISSING yet (Phase 3).
  That requires understanding the error-handling branches in each step.
"""

from __future__ import annotations

import networkx as nx

from core.schema import CollisionFinding, EdgeType, NodeType, Severity

_CALLS     = EdgeType.CALLS.value
_WRITES_TO = EdgeType.WRITES_TO.value


def _downstream_services(graph: nx.MultiDiGraph, coordinator_id: str) -> list[str]:
    """Return all services that the coordinator has CALLS edges to."""
    downstream = []
    for _, target, data in graph.out_edges(coordinator_id, data=True):
        if data.get("edge_type") == _CALLS:
            ttype = graph.nodes.get(target, {}).get("node_type", "")
            if ttype == NodeType.SERVICE.value:
                downstream.append(target)
    return downstream


def _writes_of(graph: nx.MultiDiGraph, service_id: str) -> set[str]:
    """All table IDs that a service writes to."""
    targets = set()
    for _, target, data in graph.out_edges(service_id, data=True):
        if data.get("edge_type") == _WRITES_TO:
            if graph.nodes.get(target, {}).get("node_type") == NodeType.TABLE.value:
                targets.add(target)
    return targets


def detect_saga_coordinators(graph: nx.MultiDiGraph) -> set[str]:
    """
    Returns the set of service node IDs identified as Saga coordinators.

    A service qualifies if:
      - It has CALLS edges to >= 2 other services
      - Those downstream services collectively write to >= 2 different tables
      - The coordinator itself does not directly write to those same tables
        (pure orchestration, not direct participation)
    """
    coordinators: set[str] = set()

    for service_id, attrs in graph.nodes(data=True):
        if attrs.get("node_type") != NodeType.SERVICE.value:
            continue

        downstream = _downstream_services(graph, service_id)
        if len(downstream) < 2:
            continue

        # Tables written by all downstream services combined
        downstream_writes: set[str] = set()
        for svc in downstream:
            downstream_writes |= _writes_of(graph, svc)

        if len(downstream_writes) < 2:
            continue  # Doesn't look like a multi-step saga

        # Coordinator must NOT directly write to those tables (pure orchestrator)
        own_writes = _writes_of(graph, service_id)
        if own_writes & downstream_writes:
            continue  # Coordinator participates directly — not a pure saga

        coordinators.add(service_id)

    return coordinators


def suppress_saga_findings(
    findings: list[CollisionFinding],
    graph: nx.MultiDiGraph,
    warnings: list[str],
) -> list[CollisionFinding]:
    """
    Reclassify race condition findings involving Saga coordinator downstream
    services from CRITICAL to INFO, and mark them as suppressed.

    Also emits a WARNING advisory: "Saga detected — verify compensating
    transactions exist for each step."
    """
    coordinators = detect_saga_coordinators(graph)
    if not coordinators:
        return findings

    # Build the set of all services downstream of a coordinator
    coordinated_services: set[str] = set()
    for coord_id in coordinators:
        for svc in _downstream_services(graph, coord_id):
            coordinated_services.add(svc)
        coord_name = graph.nodes[coord_id].get("name", coord_id)
        warnings.append(
            f"SAGA: '{coord_name}' detected as a Saga coordinator. "
            f"Downstream write races suppressed. "
            f"Verify compensating transactions exist for each saga step."
        )

    for finding in findings:
        a1 = finding.actor_1_id
        a2 = finding.actor_2_id
        # If both actors are downstream of the same coordinator, suppress
        if a1 in coordinated_services and a2 in coordinated_services:
            finding.suppressed = True
            finding.severity   = Severity.INFO
            finding.suppression_reason = (
                "Both actors are downstream steps of a detected Saga coordinator. "
                "The writes are orchestrated and sequential, not concurrent races."
            )

    return findings
