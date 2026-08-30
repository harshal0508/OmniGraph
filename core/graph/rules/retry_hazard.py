"""
core/graph/rules/retry_hazard.py
-----------------------------------------------------------------------------
Retry-Amplification Hazard Detector  (EC-14)

THE PROBLEM:
  Retry logic on non-idempotent writes is a silent multiplier of race conditions.

  Normal race:   2 replicas -> 2 concurrent writes -> 1 lost update
  With retries:  2 replicas x 3 retries each -> up to 6 concurrent writes
                 -> dramatically higher collision probability
                 -> AND potential for duplicate processing (double charges,
                    double-sends, duplicate records)

  A write is IDEMPOTENT if calling it multiple times produces the same result.
    - Idempotent:     UPDATE users SET status='verified' WHERE id=42
    - Non-idempotent: INSERT INTO events (...) VALUES (...)
                      UPDATE wallet SET balance = balance - 50 WHERE id=42
                      INCR counter

THE DETECTION PATTERN:
  We look for services that:
    1. Have WRITES_TO edges with non-idempotent patterns (INSERT, INCR, bulk ops)
    2. AND have retry-indicating metadata on those edges
       (set by the AST parser when it detects retry decorators/wrappers)
    3. AND have no idempotency key / unique constraint marker

  In Phase 2 (prototype), the AST parser doesn't yet detect retry wrappers
  natively, so we use a conservative heuristic:
    - Service has WRITES_TO with INSERT / bulk_create / insertMany patterns
    - AND replica_count > 1
    => Flag as RETRY_HAZARD_POTENTIAL at WARNING level

  Phase 3 will add active retry decorator/wrapper detection.
"""

from __future__ import annotations

import networkx as nx

from core.schema import (
    CollisionFinding, EvidencePath, EdgeType, NodeType, Severity,
)

_WRITES_TO = EdgeType.WRITES_TO.value

# ORM patterns that produce non-idempotent writes
# (inserting new rows or raw increments — not safe to retry blindly)
_NON_IDEMPOTENT_PATTERNS = frozenset({
    # Python
    "django.create()",
    "django.bulk_create()",
    "sqlalchemy.session.add()",
    "tortoise.create()",
    # JavaScript
    "sequelize.create()",
    "sequelize.bulkcreate()",
    "typeorm.insert()",
    "prisma.create()",
    "prisma.createmany()",
    "mongoose.insertmany()",
    "knex.insert()",
    # Raw SQL
    "raw_sql_write",
    "raw_sql_write_regex",
})

# Patterns that are inherently idempotent (safe to retry)
_IDEMPOTENT_PATTERNS = frozenset({
    "django.update()",
    "django.update_or_create()",
    "django.save()",
    "sqlalchemy.session.merge()",
    "sequelize.upsert()",
    "typeorm.save()",
    "prisma.upsert()",
    "prisma.update()",
    "prisma.updatemany()",
})


def _is_non_idempotent(pattern: str) -> bool:
    """True if the write pattern is known to be non-idempotent."""
    p = pattern.lower()
    # Check explicit non-idempotent list first
    if p in _NON_IDEMPOTENT_PATTERNS:
        return True
    # Check explicit idempotent list (safe - exclude)
    if p in _IDEMPOTENT_PATTERNS:
        return False
    # Conservative: raw SQL writes of unknown type are assumed non-idempotent
    if "insert" in p or "bulk" in p:
        return True
    return False


def detect_retry_hazards(
    graph: nx.MultiDiGraph,
) -> list[CollisionFinding]:
    """
    Flag services with non-idempotent writes on multi-replica deployments.

    These are not confirmed races — they are architectural hazards that become
    races when combined with retry logic (timeouts, network blips, etc.).
    All findings are emitted as WARNING severity.
    """
    findings: list[CollisionFinding] = []
    seen: set[frozenset] = set()

    for service_id, attrs in graph.nodes(data=True):
        if attrs.get("node_type") != NodeType.SERVICE.value:
            continue

        replica_count = attrs.get("replica_count", 1)
        if replica_count <= 1:
            continue   # Retry hazard only relevant for multi-replica services

        # Collect non-idempotent write edges
        hazard_edges: list[dict] = []
        for _, target, data in graph.out_edges(service_id, data=True):
            if data.get("edge_type") != _WRITES_TO:
                continue
            pattern = data.get("pattern", "")
            if _is_non_idempotent(pattern):
                target_type = graph.nodes.get(target, {}).get("node_type", "")
                if target_type == NodeType.TABLE.value:
                    hazard_edges.append({**data, "_target": target})

        if not hazard_edges:
            continue

        # Deduplicate by (service, table) pair
        for data in hazard_edges:
            target_id = data.get("_target", "unknown")
            pair = frozenset({service_id, target_id, "retry_hazard"})
            if pair in seen:
                continue
            seen.add(pair)

            table_name = graph.nodes.get(target_id, {}).get("name", target_id)
            evidence = [EvidencePath(
                file=data.get("source_file", "unknown"),
                line=data.get("source_line"),
                description=(
                    f"Non-idempotent write [{data.get('pattern', '')}] to "
                    f"'{table_name}' on a {replica_count}-replica service. "
                    f"Retries on failure will produce duplicate records."
                ),
            )]

            findings.append(CollisionFinding(
                collision_type="Retry-Amplification Hazard",
                actor_1_id=service_id,
                actor_2_id=service_id,
                shared_target_id=target_id,
                atomic_protection=False,
                confidence=0.65,   # Moderate — hazard, not confirmed race
                evidence=evidence,
                severity=Severity.WARNING,   # Always WARNING, never CRITICAL alone
                suppressed=False,
                remediation_hint=(
                    f"Protect the non-idempotent write to '{table_name}' with an "
                    f"idempotency key: add a unique constraint on a client-generated "
                    f"request_id column and use INSERT ... ON CONFLICT DO NOTHING, OR "
                    f"switch to an upsert pattern "
                    f"(Django: update_or_create, Prisma: upsert, SQLAlchemy: merge). "
                    f"This ensures retries are safe regardless of replica count."
                ),
            ))

    return findings
