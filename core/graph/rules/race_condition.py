"""
core/graph/rules/race_condition.py
─────────────────────────────────────────────────────────────────────────────
Core Collision Detection Rules.

Implements the mathematical proof from the OmniGraph spec:

  (Service_A --WRITES_TO--> Table_X)
  AND
  (Service_B --WRITES_TO--> Table_X)
  AND
  Service_A.id != Service_B.id
  AND
  NOT (Service_A --USES_LOCK--> Mutex)
  AND
  NOT (Service_B --USES_LOCK--> Mutex)
  AND
  NOT (Service_A.has_transaction == True AND Service_B.has_transaction == True)
  AND
  (Service_A.replica_count > 1 OR Service_B.replica_count > 1)

  => DISTRIBUTED RACE CONDITION DETECTED
"""

from __future__ import annotations

from typing import Iterator

import networkx as nx

from core.schema import (
    CollisionFinding, EvidencePath, EdgeType,
    NodeType, Severity, DbIsolationLevel,
)


# ─── Edge Type Constants (string form stored in graph) ────────────────────────

_WRITES_TO        = EdgeType.WRITES_TO.value
_READS_FROM       = EdgeType.READS_FROM.value
_USES_LOCK        = EdgeType.USES_LOCK.value
_USES_TRANSACTION = EdgeType.USES_TRANSACTION.value
_PUBLISHES_TO     = EdgeType.PUBLISHES_TO.value
_CONSUMED_BY      = EdgeType.CONSUMED_BY.value


# ─── Helper Queries ───────────────────────────────────────────────────────────

def _get_writers(graph: nx.MultiDiGraph, target_id: str) -> list[str]:
    """
    Returns all service node IDs that have a WRITES_TO edge pointing at target_id.
    """
    writers = []
    for source, target, attrs in graph.in_edges(target_id, data=True):
        if attrs.get("edge_type") == _WRITES_TO:
            source_type = graph.nodes[source].get("node_type", "")
            if source_type == NodeType.SERVICE.value:
                writers.append(source)
            elif source_type == "Unknown" or source_type == NodeType.FUNCTION.value:
                # Trace back to service. E.g. func_test_svc_checkout_process
                parts = source.split('_')
                svc_id = "svc_" + parts[2] if parts[1] == "svc" else parts[1]
                writers.append(svc_id)
                
    return list(set(writers))


def _has_lock(graph: nx.MultiDiGraph, service_id: str) -> bool:
    """
    Returns True if the service has a USES_LOCK edge to any Mutex node.
    """
    for _, target, attrs in graph.out_edges(service_id, data=True):
        if attrs.get("edge_type") == _USES_LOCK:
            return True
    return False


def _has_transaction(graph: nx.MultiDiGraph, service_id: str) -> bool:
    """Returns True if the service node is marked as transaction-protected."""
    return bool(graph.nodes[service_id].get("has_transaction", False))


def _is_queue_mediated(
    graph: nx.MultiDiGraph,
    service_a: str,
    service_b: str,
    target: str,
) -> bool:
    """
    Returns True if the write path passes through a Queue node,
    indicating intentional async decoupling (EC-3).
    A queue-mediated write is NOT a race condition.
    """
    queue_nodes = {
        n for n, attr in graph.nodes(data=True)
        if attr.get("node_type") == NodeType.QUEUE.value
    }
    if not queue_nodes:
        return False

    # Check if either writer routes through a queue before hitting the target
    for svc in (service_a, service_b):
        for _, q_target, attrs in graph.out_edges(svc, data=True):
            if q_target in queue_nodes and attrs.get("edge_type") == _PUBLISHES_TO:
                # Check if that queue connects to the target
                for _, final_target, attrs2 in graph.out_edges(q_target, data=True):
                    if final_target == target:
                        return True
    return False


def _get_write_evidence(
    graph: nx.MultiDiGraph,
    service_id: str,
    target_id: str,
) -> list[EvidencePath]:
    """Collects file/line evidence for a WRITES_TO edge."""
    evidence = []
    for src, tgt, attrs in graph.out_edges(service_id, data=True):
        if tgt == target_id and attrs.get("edge_type") == _WRITES_TO:
            evidence.append(EvidencePath(
                file=attrs.get("source_file", "unknown"),
                line=attrs.get("source_line") or None,
                description=f"{graph.nodes[service_id]['name']} writes to {graph.nodes[target_id]['name']} "
                            f"via {attrs.get('pattern', 'unknown pattern')}",
            ))
    return evidence


def _compute_confidence(
    replica_a: int,
    replica_b: int,
    has_lock_a: bool,
    has_lock_b: bool,
    has_txn_a: bool,
    has_txn_b: bool,
    is_queue: bool,
) -> float:
    """
    Heuristic confidence score (0.0 – 1.0) for a detected collision.

    Starts at 1.0 and is reduced by mitigating factors.
    Findings below 0.65 are filed as WARNING, not CRITICAL.
    """
    score = 1.0

    # Replica count multiplier — single-replica services can't race with each other
    if replica_a == 1 and replica_b == 1:
        score -= 0.40   # Both single-replica — very low collision risk
    elif replica_a == 1 or replica_b == 1:
        score -= 0.15   # One is single-replica — reduced risk

    # Partial lock detection reduces confidence (lock may protect things)
    if has_lock_a or has_lock_b:
        score -= 0.20

    # Transaction detection — partial mitigation (EC DB isolation)
    if has_txn_a or has_txn_b:
        score -= 0.10

    # Queue-mediated is a very strong suppressor (handled separately, but just in case)
    if is_queue:
        score -= 0.60

    return max(0.0, round(score, 2))


# ─── Main Detection Rule ──────────────────────────────────────────────────────

def detect_race_conditions(
    graph: nx.MultiDiGraph,
) -> list[CollisionFinding]:
    """
    Runs the core distributed race condition detection rule over the full graph.

    Algorithm:
      1. Find all TABLE nodes (shared datastores)
      2. For each table, find all SERVICE nodes that WRITE_TO it
      3. For each pair of distinct writers:
         a. Check no USES_LOCK or USES_TRANSACTION on either
         b. Check at least one writer has replica_count > 1
         c. Check write path is not queue-mediated
         d. Compute confidence score
         e. Emit a CollisionFinding
    """
    findings: list[CollisionFinding] = []
    seen_pairs: set[frozenset] = set()

    # Iterate over all TABLE nodes
    table_nodes = [
        node_id for node_id, attrs in graph.nodes(data=True)
        if attrs.get("node_type") == NodeType.TABLE.value
    ]

    for table_id in table_nodes:
        writers = _get_writers(graph, table_id)

        if len(writers) < 2:
            continue  # Single writer — no cross-service collision possible

        # Check every pair of distinct writers
        for i in range(len(writers)):
            for j in range(i + 1, len(writers)):
                svc_a = writers[i]
                svc_b = writers[j]

                # Deduplicate — same pair, same table
                pair_key = frozenset({svc_a, svc_b, table_id})
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                lock_a  = _has_lock(graph, svc_a)
                lock_b  = _has_lock(graph, svc_b)
                txn_a   = _has_transaction(graph, svc_a)
                txn_b   = _has_transaction(graph, svc_b)
                is_queue = _is_queue_mediated(graph, svc_a, svc_b, table_id)

                replica_a = graph.nodes[svc_a].get("replica_count", 1)
                replica_b = graph.nodes[svc_b].get("replica_count", 1)

                confidence = _compute_confidence(
                    replica_a, replica_b,
                    lock_a, lock_b,
                    txn_a, txn_b,
                    is_queue,
                )

                # Build evidence paths
                evidence = (
                    _get_write_evidence(graph, svc_a, table_id)
                    + _get_write_evidence(graph, svc_b, table_id)
                )

                # Determine if fully protected
                atomic_protection = (lock_a and lock_b) or (txn_a and txn_b)

                # Determine severity
                if atomic_protection or is_queue:
                    severity = Severity.INFO
                elif confidence < 0.65:
                    severity = Severity.WARNING
                else:
                    severity = Severity.CRITICAL

                # Remediation hint
                hint = _generate_remediation_hint(lock_a, lock_b, txn_a, txn_b, replica_a, replica_b)

                findings.append(CollisionFinding(
                    collision_type="Distributed Race Condition",
                    actor_1_id=svc_a,
                    actor_2_id=svc_b,
                    shared_target_id=table_id,
                    atomic_protection=atomic_protection,
                    confidence=confidence,
                    evidence=evidence,
                    severity=severity,
                    suppressed=atomic_protection or is_queue,
                    suppression_reason=(
                        "Queue-mediated write (intentional async decoupling)" if is_queue
                        else "Both services use atomic protection" if atomic_protection
                        else None
                    ),
                    remediation_hint=hint,
                ))

    return findings


def _generate_remediation_hint(
    lock_a: bool, lock_b: bool,
    txn_a: bool, txn_b: bool,
    replica_a: int, replica_b: int,
) -> str:
    """Generate a concise remediation hint based on the detection context."""
    if not lock_a and not lock_b:
        if replica_a > 1 or replica_b > 1:
            return (
                "Introduce a distributed atomic lock (e.g., Redis Redlock or PostgreSQL "
                "advisory lock) around the shared write operation, OR restructure using "
                "the Saga pattern with compensating transactions."
            )
        return (
            "Consider wrapping the write in a database transaction with SELECT FOR UPDATE "
            "to prevent concurrent modifications."
        )
    if lock_a and not lock_b:
        return f"Service writing without a lock detected. Add lock acquisition before the write operation."
    if not lock_a and lock_b:
        return f"Service writing without a lock detected. Add lock acquisition before the write operation."
    return "Verify that both lock acquisitions share the same lock namespace/key."
