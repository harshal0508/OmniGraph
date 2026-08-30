"""
core/graph/rules/toctou.py
-----------------------------------------------------------------------------
TOCTOU (Time-of-Check-Time-of-Use) Race Condition Detector.

EC-7 from the OmniGraph edge case registry.

THE PATTERN:
  A service reads a record, checks a condition on it, then writes back to it
  - without a distributed lock or SELECT FOR UPDATE protecting the gap.

  Read --> [check value] --> Write   <-- race window here
                            ^
                   Another replica does the same
                   read at this exact moment, also
                   passes the check, also writes.

CLASSIC EXAMPLES:
  - Payment: read status="pending" -> check -> set status="paid" (double charge)
  - Inventory: read stock=5 -> check stock>=1 -> decrement (oversell)
  - Wallet: read balance=100 -> check balance>=amount -> deduct (overdraft)

GRAPH-LEVEL DETECTION:
  Because we don't do full control-flow analysis within functions yet,
  we detect TOCTOU at the service level:

    Service has READS_FROM edge  ---> Table X
    Service has WRITES_TO  edge  ---> Table X   (same table)
    Service has NO USES_LOCK edge
    Service has NO has_transaction flag
    => TOCTOU_VULNERABLE

  This is conservative but catches the most dangerous cases.
  Phase 3 will add per-function CFG analysis for higher precision.
"""

from __future__ import annotations

import networkx as nx

from core.schema import (
    CollisionFinding, EvidencePath, EdgeType, NodeType, Severity,
)

# Edge type string values stored in graph
_READS_FROM  = EdgeType.READS_FROM.value
_WRITES_TO   = EdgeType.WRITES_TO.value
_USES_LOCK   = EdgeType.USES_LOCK.value


def _service_read_targets(graph: nx.MultiDiGraph, service_id: str) -> set[str]:
    """All TABLE node IDs that this service has a READS_FROM edge to."""
    targets = set()
    for _, target, data in graph.out_edges(service_id, data=True):
        if data.get("edge_type") == _READS_FROM:
            node_type = graph.nodes.get(target, {}).get("node_type", "")
            if node_type == NodeType.TABLE.value:
                targets.add(target)
    return targets


def _service_write_targets(graph: nx.MultiDiGraph, service_id: str) -> set[str]:
    """All TABLE node IDs that this service has a WRITES_TO edge to."""
    targets = set()
    for _, target, data in graph.out_edges(service_id, data=True):
        if data.get("edge_type") == _WRITES_TO:
            node_type = graph.nodes.get(target, {}).get("node_type", "")
            if node_type == NodeType.TABLE.value:
                targets.add(target)
    return targets


def _service_has_lock(graph: nx.MultiDiGraph, service_id: str) -> bool:
    for _, _, data in graph.out_edges(service_id, data=True):
        if data.get("edge_type") == _USES_LOCK:
            return True
    return False


def _gather_rw_evidence(
    graph: nx.MultiDiGraph,
    service_id: str,
    table_id: str,
) -> list[EvidencePath]:
    """Collect read AND write evidence paths for a specific service-table pair."""
    evidence = []
    table_name = graph.nodes[table_id].get("name", table_id)

    for _, target, data in graph.out_edges(service_id, data=True):
        if target != table_id:
            continue
        et = data.get("edge_type", "")
        if et == _READS_FROM:
            evidence.append(EvidencePath(
                file=data.get("source_file", "unknown"),
                line=data.get("source_line"),
                description=f"READ from '{table_name}' — start of TOCTOU window "
                            f"[{data.get('pattern', '')}]",
            ))
        elif et == _WRITES_TO:
            evidence.append(EvidencePath(
                file=data.get("source_file", "unknown"),
                line=data.get("source_line"),
                description=f"WRITE to '{table_name}' — closes TOCTOU window unprotected "
                            f"[{data.get('pattern', '')}]",
            ))
    return evidence


def _toctou_confidence(replica_count: int, has_lock: bool, has_txn: bool) -> float:
    """Confidence score for a TOCTOU finding."""
    score = 0.85  # Start lower than distributed race — single service is less certain
    if replica_count <= 1:
        score -= 0.30  # Lower risk if single replica (but still possible via async)
    if has_lock or has_txn:
        score -= 0.40  # Partial protection present
    return max(0.0, round(score, 2))


def detect_toctou(graph: nx.MultiDiGraph) -> list[CollisionFinding]:
    """
    Detect TOCTOU (Time-of-Check-Time-of-Use) patterns in the graph.

    For each SERVICE node:
      1. Find tables it reads from
      2. Find tables it writes to
      3. Intersection = tables where read-then-write exists
      4. If no lock/transaction protects the service -> TOCTOU_VULNERABLE
    """
    findings: list[CollisionFinding] = []

    for service_id, attrs in graph.nodes(data=True):
        if attrs.get("node_type") != NodeType.SERVICE.value:
            continue

        reads  = _service_read_targets(graph, service_id)
        writes = _service_write_targets(graph, service_id)

        # Tables that are both read AND written by this service = TOCTOU candidates
        toctou_tables = reads & writes
        if not toctou_tables:
            continue

        has_lock = _service_has_lock(graph, service_id)
        has_txn  = bool(attrs.get("has_transaction", False))

        # If protected by either a lock OR a transaction, skip
        if has_lock or has_txn:
            continue

        replica_count = attrs.get("replica_count", 1)

        for table_id in toctou_tables:
            table_name   = graph.nodes[table_id].get("name", table_id)
            svc_name     = attrs.get("name", service_id)
            confidence   = _toctou_confidence(replica_count, has_lock, has_txn)
            evidence     = _gather_rw_evidence(graph, service_id, table_id)

            # Severity: CRITICAL if multi-replica and unprotected, else WARNING
            if replica_count > 1 and not has_lock and not has_txn:
                severity = Severity.CRITICAL
            elif confidence >= 0.50:
                severity = Severity.WARNING
            else:
                severity = Severity.INFO

            findings.append(CollisionFinding(
                collision_type="TOCTOU Race Condition",
                actor_1_id=service_id,
                actor_2_id=service_id,   # Same service — self-race across replicas
                shared_target_id=table_id,
                atomic_protection=False,
                confidence=confidence,
                evidence=evidence,
                severity=severity,
                suppressed=False,
                remediation_hint=(
                    "Wrap the read-check-write sequence in a SELECT FOR UPDATE "
                    f"(Django: queryset.select_for_update(), "
                    f"SQLAlchemy: query.with_for_update(), "
                    f"TypeORM: findOne({{ lock: {{ mode: 'pessimistic_write' }} }})) "
                    f"OR acquire a distributed Redis lock before reading "
                    f"'{table_name}' to close the TOCTOU window."
                ),
            ))

    return findings
