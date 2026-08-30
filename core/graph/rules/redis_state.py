"""
core/graph/rules/redis_state.py
-----------------------------------------------------------------------------
Redis as Mutable Shared State Detector  (EC-13)

THE PROBLEM:
  Developers often treat Redis as a "fast cache" — but when they use it as a
  primary mutable store with read-modify-write patterns, it becomes a shared
  datastore with the same race risks as PostgreSQL.

  Example race:
    Replica A: GET wallet:user:42      => "100"
    Replica B: GET wallet:user:42      => "100"
    Replica A: SET wallet:user:42 "50" (deducted 50)
    Replica B: SET wallet:user:42 "50" (also deducted 50 — lost Replica A write!)
    Result: wallet shows 50 instead of 0. Money created from nothing.

SAFE vs UNSAFE Redis operations:
  SAFE  (atomic by nature):  INCR, DECR, INCRBY, DECRBY, SETNX, SET NX,
                              GETSET, RPUSH, LPUSH, SADD, ZADD
  UNSAFE (non-atomic r-m-w): GET then SET, HGET then HSET without WATCH/MULTI
  PROTECTED:                 WATCH + MULTI + EXEC (optimistic locking),
                             Lua scripts (executed atomically by Redis)

GRAPH-LEVEL DETECTION:
  A Redis node is flagged as MUTABLE_SHARED_STATE when:
    - A service has a WRITES_TO edge to a Redis node  (SET, HSET etc.)
    - AND a READS_FROM edge to the same Redis node    (GET, HGET etc.)
    - AND no USES_LOCK edge exists on the service
    - AND the Redis write pattern is NOT one of the inherently atomic commands
"""

from __future__ import annotations

import networkx as nx

from core.schema import (
    CollisionFinding, EvidencePath, EdgeType, NodeType, Severity,
)

_READS_FROM  = EdgeType.READS_FROM.value
_WRITES_TO   = EdgeType.WRITES_TO.value
_USES_LOCK   = EdgeType.USES_LOCK.value

# Redis commands that are inherently atomic - no race risk
_ATOMIC_REDIS_COMMANDS = frozenset({
    "incr", "decr", "incrby", "decrby", "incrbyfloat",
    "setnx", "msetnx", "getset", "getdel",
    "rpush", "lpush", "rpushx", "lpushx",
    "sadd", "srem", "smembers",
    "zadd", "zrem", "zincrby",
    "hincrby", "hincrbyfloat",
    "append",
})


def _is_redis_node(graph: nx.MultiDiGraph, node_id: str) -> bool:
    """True if the node is a Database or Queue node backed by Redis."""
    attrs = graph.nodes.get(node_id, {})
    node_type = attrs.get("node_type", "")
    if node_type not in (NodeType.DATABASE.value, NodeType.QUEUE.value):
        return False
    name = attrs.get("name", "").lower()
    db_type = attrs.get("db_type", attrs.get("broker_type", "")).lower()
    return "redis" in name or "redis" in db_type


def _get_redis_write_edges(
    graph: nx.MultiDiGraph,
    service_id: str,
    redis_id: str,
) -> list[dict]:
    """Return all WRITES_TO edges from service to redis node."""
    edges = []
    for _, target, data in graph.out_edges(service_id, data=True):
        if target == redis_id and data.get("edge_type") == _WRITES_TO:
            method = data.get("method", "").lower()
            if method not in _ATOMIC_REDIS_COMMANDS:
                edges.append(data)
    return edges


def _get_redis_read_edges(
    graph: nx.MultiDiGraph,
    service_id: str,
    redis_id: str,
) -> list[dict]:
    """Return all READS_FROM edges from service to redis node."""
    edges = []
    for _, target, data in graph.out_edges(service_id, data=True):
        if target == redis_id and data.get("edge_type") == _READS_FROM:
            edges.append(data)
    return edges


def _has_lock(graph: nx.MultiDiGraph, service_id: str) -> bool:
    for _, _, data in graph.out_edges(service_id, data=True):
        if data.get("edge_type") == _USES_LOCK:
            return True
    return False


def detect_redis_state_races(
    graph: nx.MultiDiGraph,
) -> list[CollisionFinding]:
    """
    Detect non-atomic read-modify-write patterns against Redis nodes.

    A finding is emitted when a service both reads and writes to a Redis
    node using non-atomic commands, without holding a distributed lock.
    """
    findings: list[CollisionFinding] = []
    seen: set[frozenset] = set()

    # Find all Redis nodes
    redis_nodes = [
        n for n in graph.nodes
        if _is_redis_node(graph, n)
    ]

    if not redis_nodes:
        return findings

    for redis_id in redis_nodes:
        redis_name = graph.nodes[redis_id].get("name", redis_id)

        # Find services that write to this Redis node
        writers: list[str] = []
        for source, target, data in graph.in_edges(redis_id, data=True):
            if data.get("edge_type") == _WRITES_TO:
                src_type = graph.nodes.get(source, {}).get("node_type", "")
                if src_type == NodeType.SERVICE.value:
                    writers.append(source)

        for service_id in writers:
            pair = frozenset({service_id, redis_id})
            if pair in seen:
                continue
            seen.add(pair)

            # Check if service also reads from this redis node
            read_edges  = _get_redis_read_edges(graph, service_id, redis_id)
            write_edges = _get_redis_write_edges(graph, service_id, redis_id)

            if not read_edges or not write_edges:
                continue   # No read-modify-write pattern detected

            has_lock    = _has_lock(graph, service_id)
            replica_cnt = graph.nodes[service_id].get("replica_count", 1)

            if has_lock:
                continue   # Protected by a distributed lock

            # Build evidence
            evidence = []
            for data in read_edges:
                evidence.append(EvidencePath(
                    file=data.get("source_file", "unknown"),
                    line=data.get("source_line"),
                    description=f"READ from Redis '{redis_name}' "
                                f"[{data.get('pattern', data.get('method', ''))}]",
                ))
            for data in write_edges:
                evidence.append(EvidencePath(
                    file=data.get("source_file", "unknown"),
                    line=data.get("source_line"),
                    description=f"WRITE to Redis '{redis_name}' — non-atomic "
                                f"[{data.get('pattern', data.get('method', ''))}]",
                ))

            confidence = 0.80 if replica_cnt > 1 else 0.50
            severity   = Severity.CRITICAL if replica_cnt > 1 else Severity.WARNING

            findings.append(CollisionFinding(
                collision_type="Redis Non-Atomic Read-Modify-Write",
                actor_1_id=service_id,
                actor_2_id=service_id,
                shared_target_id=redis_id,
                atomic_protection=False,
                confidence=confidence,
                evidence=evidence,
                severity=severity,
                suppressed=False,
                remediation_hint=(
                    f"Replace the GET + SET pattern on Redis '{redis_name}' with: "
                    f"(1) Redis atomic commands: INCR/DECR/INCRBY for counters, "
                    f"SETNX for conditional set; "
                    f"(2) WATCH + MULTI + EXEC optimistic locking; "
                    f"(3) A Lua script (executed atomically by Redis server); OR "
                    f"(4) A distributed lock (Redlock via redis-py-lock / redlock-py) "
                    f"wrapping the full read-modify-write sequence."
                ),
            ))

    return findings
