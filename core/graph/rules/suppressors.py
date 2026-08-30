"""
core/graph/rules/suppressors.py
─────────────────────────────────────────────────────────────────────────────
Post-detection Suppression Rules.

After race_condition.py generates raw findings, this module applies
edge-case suppressors to reduce false positives:

  EC-1: DB SERIALIZABLE isolation suppressor
  EC-3: Queue-mediated write suppressor (also handled inline in race_condition.py)
  EC-4: Single-replica suppressor
  EC-10: Config environment ambiguity advisory

Each suppressor returns the (possibly modified) list of findings.
"""

from __future__ import annotations

import networkx as nx

from core.schema import (
    CollisionFinding, Severity, DbIsolationLevel, NodeType,
)


# ─── Suppressor: DB Isolation Level (EC-1) ────────────────────────────────────

def suppress_by_db_isolation(
    findings: list[CollisionFinding],
    graph: nx.MultiDiGraph,
) -> list[CollisionFinding]:
    """
    EC-1: If the shared table's parent database runs SERIALIZABLE isolation,
    the database engine itself prevents the race. Suppress or downgrade finding.
    """
    for finding in findings:
        table_id = finding.shared_target_id
        if table_id not in graph.nodes:
            continue

        db_id = graph.nodes[table_id].get("database_id", "")
        if not db_id or db_id not in graph.nodes:
            continue

        isolation = graph.nodes[db_id].get("isolation_level", DbIsolationLevel.UNKNOWN.value)

        if isolation == DbIsolationLevel.SERIALIZABLE.value:
            finding.suppressed = True
            finding.suppression_reason = (
                f"Database '{graph.nodes[db_id].get('name', db_id)}' runs SERIALIZABLE "
                "isolation — the DB engine prevents this race condition natively."
            )
            finding.severity = Severity.INFO

        elif isolation == DbIsolationLevel.REPEATABLE_READ.value:
            # Partial mitigation — phantom reads still possible
            if finding.severity == Severity.CRITICAL:
                finding.severity = Severity.WARNING
                finding.suppression_reason = (
                    "Database uses REPEATABLE_READ isolation — reduces but does not "
                    "eliminate phantom read races. Manual review recommended."
                )

    return findings


# ─── Suppressor: Single Replica (EC-4) ───────────────────────────────────────

def suppress_single_replica(
    findings: list[CollisionFinding],
    graph: nx.MultiDiGraph,
) -> list[CollisionFinding]:
    """
    EC-4: If BOTH services in a finding have replica_count == 1,
    they cannot run concurrently as separate instances.
    Downgrade to WARNING instead of CRITICAL.

    Note: Two distinct services (different code) CAN still race
    even with replica_count = 1 each — they run in parallel processes.
    We only fully suppress if same service_id races with itself and count is 1.
    """
    for finding in findings:
        svc_a = finding.actor_1_id
        svc_b = finding.actor_2_id

        replica_a = graph.nodes.get(svc_a, {}).get("replica_count", 1)
        replica_b = graph.nodes.get(svc_b, {}).get("replica_count", 1)

        if replica_a == 1 and replica_b == 1:
            # Two distinct services, both single-replica — still a potential race
            # but lower severity since they are separate processes, not parallel instances
            if finding.severity == Severity.CRITICAL and finding.confidence < 0.70:
                finding.severity = Severity.WARNING
                finding.suppression_reason = (
                    "Both services run as single replicas. Concurrent execution is "
                    "possible but less likely. Consider adding an atomic lock as a safeguard."
                )

    return findings


# ─── Suppressor: Config Environment Ambiguity (EC-10) ────────────────────────

def annotate_config_ambiguity(
    findings: list[CollisionFinding],
    graph: nx.MultiDiGraph,
    warnings: list[str],
) -> list[CollisionFinding]:
    """
    EC-10: If a service node was sourced from docker-compose.yml (dev config)
    rather than a Kubernetes prod manifest, warn that the replica count
    may differ in production.
    """
    for finding in findings:
        for svc_id in (finding.actor_1_id, finding.actor_2_id):
            if svc_id not in graph.nodes:
                continue
            source = graph.nodes[svc_id].get("source_file", "")
            replica_count = graph.nodes[svc_id].get("replica_count", 1)
            if "docker-compose" in source.lower() and replica_count == 1:
                warnings.append(
                    f"⚠️  EC-10: Service '{graph.nodes[svc_id].get('name', svc_id)}' replica "
                    f"count sourced from docker-compose (dev config). Production Kubernetes "
                    f"replicas may be higher — finding confidence may be understated."
                )

    return findings


# ─── Master Suppression Pipeline ─────────────────────────────────────────────

def apply_all_suppressors(
    findings: list[CollisionFinding],
    graph: nx.MultiDiGraph,
    warnings: list[str] | None = None,
) -> list[CollisionFinding]:
    """
    Applies all suppression rules in sequence.
    Returns the filtered/annotated findings list.
    """
    if warnings is None:
        warnings = []

    findings = suppress_by_db_isolation(findings, graph)
    findings = suppress_single_replica(findings, graph)
    findings = annotate_config_ambiguity(findings, graph, warnings)

    return findings
