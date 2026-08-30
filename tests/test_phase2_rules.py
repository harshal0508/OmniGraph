"""
tests/test_phase2_rules.py
-----------------------------------------------------------------------------
Phase 2 rule engine tests: TOCTOU, Redis state, retry hazard, Saga suppressor.
"""

from __future__ import annotations

import pytest
import networkx as nx
from pathlib import Path

from core.schema import (
    NodeType, EdgeType, Severity,
    ServiceNode, DatabaseNode, QueueNode, TableNode, GraphEdge,
)
from core.graph.builder import GraphBuilder
from core.graph.rules.toctou import detect_toctou
from core.graph.rules.redis_state import detect_redis_state_races
from core.graph.rules.retry_hazard import detect_retry_hazards
from core.graph.rules.saga_detector import detect_saga_coordinators, suppress_saga_findings
from core.ingestion.ast_parser import ASTParser

FIXTURES     = Path(__file__).parent / "fixtures"
PY_FIXTURES  = FIXTURES / "vulnerable_python"
JS_FIXTURES  = FIXTURES / "vulnerable_js"


# ─── Graph Helpers ─────────────────────────────────────────────────────────────

def _make_service(graph: nx.MultiDiGraph, svc_id: str,
                  replica_count: int = 3, has_transaction: bool = False) -> None:
    graph.add_node(svc_id, node_type=NodeType.SERVICE.value,
                   name=svc_id, replica_count=replica_count,
                   has_transaction=has_transaction, language="python")

def _make_table(graph: nx.MultiDiGraph, tbl_id: str) -> None:
    graph.add_node(tbl_id, node_type=NodeType.TABLE.value,
                   name=tbl_id, database_id="db_main")

def _make_redis(graph: nx.MultiDiGraph, redis_id: str) -> None:
    graph.add_node(redis_id, node_type=NodeType.DATABASE.value,
                   name="redis", db_type="redis")

def _add_edge(graph, src, dst, edge_type, pattern="test", src_file="test.py", src_line=1):
    graph.add_edge(src, dst,
                   edge_type=edge_type.value,
                   pattern=pattern,
                   source_file=src_file,
                   source_line=src_line)

def _add_lock(graph, svc_id):
    mutex_id = f"mutex_{svc_id}"
    graph.add_node(mutex_id, node_type=NodeType.MUTEX.value, name="lock")
    graph.add_edge(svc_id, mutex_id, edge_type=EdgeType.USES_LOCK.value)


# ─── TOCTOU Detection Tests ────────────────────────────────────────────────────

class TestTOCTOU:

    def _make_toctou_graph(self, replica_count=3, add_lock=False, add_txn=False):
        g = nx.MultiDiGraph()
        _make_service(g, "svc_a", replica_count=replica_count, has_transaction=add_txn)
        _make_table(g, "tbl_orders")
        _add_edge(g, "svc_a", "tbl_orders", EdgeType.READS_FROM, "Django.get()")
        _add_edge(g, "svc_a", "tbl_orders", EdgeType.WRITES_TO, "Django.save()")
        if add_lock:
            _add_lock(g, "svc_a")
        return g

    def test_detects_unprotected_rw_on_same_table(self):
        g = self._make_toctou_graph(replica_count=3)
        findings = detect_toctou(g)
        assert len(findings) >= 1, "Should detect TOCTOU when service reads and writes same table"

    def test_finding_type_is_toctou(self):
        g = self._make_toctou_graph(replica_count=3)
        findings = detect_toctou(g)
        assert findings[0].collision_type == "TOCTOU Race Condition"

    def test_multi_replica_is_critical(self):
        g = self._make_toctou_graph(replica_count=5)
        findings = detect_toctou(g)
        assert findings[0].severity == Severity.CRITICAL

    def test_single_replica_is_warning(self):
        g = self._make_toctou_graph(replica_count=1)
        findings = detect_toctou(g)
        assert findings[0].severity == Severity.WARNING

    def test_lock_suppresses_toctou(self):
        """A service holding a distributed lock is protected — no TOCTOU."""
        g = self._make_toctou_graph(replica_count=5, add_lock=True)
        findings = detect_toctou(g)
        assert len(findings) == 0, "Lock-protected service should not produce TOCTOU finding"

    def test_transaction_suppresses_toctou(self):
        """A service with a transaction wrapper is protected."""
        g = self._make_toctou_graph(replica_count=5, add_txn=True)
        findings = detect_toctou(g)
        assert len(findings) == 0, "Transaction-wrapped service should not produce TOCTOU finding"

    def test_write_only_no_toctou(self):
        """A service that only writes (no read) is not a TOCTOU."""
        g = nx.MultiDiGraph()
        _make_service(g, "svc_write", replica_count=3)
        _make_table(g, "tbl_log")
        _add_edge(g, "svc_write", "tbl_log", EdgeType.WRITES_TO)
        findings = detect_toctou(g)
        assert len(findings) == 0, "Write-only service has no TOCTOU pattern"

    def test_read_only_no_toctou(self):
        """A service that only reads is not a TOCTOU."""
        g = nx.MultiDiGraph()
        _make_service(g, "svc_read", replica_count=3)
        _make_table(g, "tbl_data")
        _add_edge(g, "svc_read", "tbl_data", EdgeType.READS_FROM)
        findings = detect_toctou(g)
        assert len(findings) == 0, "Read-only service has no TOCTOU pattern"

    def test_evidence_contains_both_read_and_write(self):
        g = self._make_toctou_graph(replica_count=3)
        findings = detect_toctou(g)
        types = {e.description for e in findings[0].evidence}
        has_read  = any("READ"  in d for d in types)
        has_write = any("WRITE" in d for d in types)
        assert has_read  and has_write, "Evidence must include both read and write paths"

    def test_toctou_from_fixture_file(self):
        """Parse the actual toctou_service.py fixture and verify detection."""
        parser = ASTParser()
        parsed = parser.parse_file(
            PY_FIXTURES / "toctou_service.py",
            service_id="svc_toctou",
        )
        reads  = [e for e in parsed.edges if e.edge_type == EdgeType.READS_FROM]
        writes = [e for e in parsed.edges if e.edge_type == EdgeType.WRITES_TO]
        assert reads,  "toctou_service.py should produce READS_FROM edges"
        assert writes, "toctou_service.py should produce WRITES_TO edges"

    def test_confidence_in_valid_range(self):
        g = self._make_toctou_graph(replica_count=5)
        findings = detect_toctou(g)
        for f in findings:
            assert 0.0 <= f.confidence <= 1.0


# ─── Redis State Detection Tests ───────────────────────────────────────────────

class TestRedisState:

    def _make_redis_graph(self, replica_count=3, add_lock=False, read_only=False):
        g = nx.MultiDiGraph()
        _make_service(g, "svc_cache", replica_count=replica_count)
        _make_redis(g, "redis_main")
        _add_edge(g, "svc_cache", "redis_main", EdgeType.READS_FROM,
                  "Redis.get()", "wallet.py", 10)
        if not read_only:
            _add_edge(g, "svc_cache", "redis_main", EdgeType.WRITES_TO,
                      "Redis.set()", "wallet.py", 15)
        if add_lock:
            _add_lock(g, "svc_cache")
        return g

    def test_detects_non_atomic_redis_rw(self):
        g = self._make_redis_graph()
        findings = detect_redis_state_races(g)
        assert len(findings) >= 1, "Should detect Redis GET+SET non-atomic race"

    def test_collision_type_is_redis(self):
        g = self._make_redis_graph()
        findings = detect_redis_state_races(g)
        assert "Redis" in findings[0].collision_type

    def test_multi_replica_redis_is_critical(self):
        g = self._make_redis_graph(replica_count=5)
        findings = detect_redis_state_races(g)
        assert findings[0].severity == Severity.CRITICAL

    def test_lock_protects_redis(self):
        g = self._make_redis_graph(add_lock=True)
        findings = detect_redis_state_races(g)
        assert len(findings) == 0, "Lock-protected Redis access should not produce finding"

    def test_read_only_redis_no_finding(self):
        g = self._make_redis_graph(read_only=True)
        findings = detect_redis_state_races(g)
        assert len(findings) == 0, "Read-only Redis access has no race"

    def test_no_redis_node_no_finding(self):
        g = nx.MultiDiGraph()
        _make_service(g, "svc_x", replica_count=3)
        _make_table(g, "tbl_x")
        _add_edge(g, "svc_x", "tbl_x", EdgeType.WRITES_TO)
        findings = detect_redis_state_races(g)
        assert len(findings) == 0, "Graph with no Redis node should not produce Redis findings"


# ─── Retry Hazard Detection Tests ─────────────────────────────────────────────

class TestRetryHazard:

    def _make_retry_graph(self, pattern="Django.create()", replica_count=3):
        g = nx.MultiDiGraph()
        _make_service(g, "svc_events", replica_count=replica_count)
        _make_table(g, "tbl_events")
        _add_edge(g, "svc_events", "tbl_events", EdgeType.WRITES_TO,
                  pattern, "events.py", 42)
        return g

    def test_detects_non_idempotent_insert_multi_replica(self):
        g = self._make_retry_graph(pattern="Django.create()", replica_count=3)
        findings = detect_retry_hazards(g)
        assert len(findings) >= 1, "Non-idempotent INSERT on multi-replica should be flagged"

    def test_finding_is_warning_not_critical(self):
        """Retry hazard is always a WARNING — not a confirmed race."""
        g = self._make_retry_graph(replica_count=5)
        findings = detect_retry_hazards(g)
        for f in findings:
            assert f.severity == Severity.WARNING, \
                "Retry hazard should be WARNING, not CRITICAL"

    def test_single_replica_no_retry_hazard(self):
        """Single-replica services are exempt — retry races need concurrency."""
        g = self._make_retry_graph(replica_count=1)
        findings = detect_retry_hazards(g)
        assert len(findings) == 0, "Single replica should not produce retry hazard"

    def test_idempotent_pattern_not_flagged(self):
        """Update/upsert patterns are idempotent — should not be flagged."""
        g = self._make_retry_graph(pattern="Prisma.upsert()", replica_count=5)
        findings = detect_retry_hazards(g)
        assert len(findings) == 0, "Idempotent upsert should not be flagged as retry hazard"

    def test_raw_sql_insert_flagged(self):
        g = self._make_retry_graph(pattern="raw_sql_write", replica_count=3)
        findings = detect_retry_hazards(g)
        assert len(findings) >= 1, "Raw SQL INSERT should be flagged as retry hazard"

    def test_remediation_mentions_idempotency_key(self):
        g = self._make_retry_graph(replica_count=3)
        findings = detect_retry_hazards(g)
        assert findings
        hint = findings[0].remediation_hint.lower()
        assert "idempotency" in hint or "upsert" in hint, \
            "Remediation must mention idempotency key or upsert pattern"

    def test_retry_fixture_produces_writes(self):
        """Parse retry_service.js and verify non-idempotent writes detected."""
        parser = ASTParser()
        result = parser.parse_file(
            JS_FIXTURES / "retry_service.js",
            service_id="svc_retry",
        )
        writes = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        assert writes, "retry_service.js should produce WRITES_TO edges from Knex.insert"


# ─── Saga Detector Tests ───────────────────────────────────────────────────────

class TestSagaDetector:

    def _make_saga_graph(self) -> nx.MultiDiGraph:
        """
        Build a Saga graph:
          coordinator_svc --CALLS--> step_a
          coordinator_svc --CALLS--> step_b
          step_a --WRITES_TO--> tbl_payments
          step_b --WRITES_TO--> tbl_inventory
        """
        g = nx.MultiDiGraph()
        # Coordinator (pure orchestrator — no direct writes)
        g.add_node("svc_coordinator", node_type=NodeType.SERVICE.value,
                   name="order_saga", replica_count=2)
        # Step services
        _make_service(g, "svc_step_a", replica_count=2)
        _make_service(g, "svc_step_b", replica_count=2)
        # Tables
        _make_table(g, "tbl_payments")
        _make_table(g, "tbl_inventory")
        # Coordinator calls steps
        g.add_edge("svc_coordinator", "svc_step_a", edge_type=EdgeType.CALLS.value)
        g.add_edge("svc_coordinator", "svc_step_b", edge_type=EdgeType.CALLS.value)
        # Steps write to different tables
        _add_edge(g, "svc_step_a", "tbl_payments",  EdgeType.WRITES_TO)
        _add_edge(g, "svc_step_b", "tbl_inventory", EdgeType.WRITES_TO)
        return g

    def test_detects_coordinator(self):
        g = self._make_saga_graph()
        coordinators = detect_saga_coordinators(g)
        assert "svc_coordinator" in coordinators, "Should detect the saga coordinator"

    def test_direct_writer_not_coordinator(self):
        """A service that directly writes to the same tables is not a pure coordinator."""
        g = self._make_saga_graph()
        # Make coordinator also write to payments (violates pure orchestrator rule)
        _add_edge(g, "svc_coordinator", "tbl_payments", EdgeType.WRITES_TO)
        coordinators = detect_saga_coordinators(g)
        assert "svc_coordinator" not in coordinators, \
            "Direct-writing coordinator should not be flagged as pure saga coordinator"

    def test_single_downstream_not_saga(self):
        """A service calling only one downstream service is not a saga coordinator."""
        g = nx.MultiDiGraph()
        _make_service(g, "svc_one", replica_count=2)
        _make_service(g, "svc_downstream", replica_count=2)
        _make_table(g, "tbl_x")
        g.add_edge("svc_one", "svc_downstream", edge_type=EdgeType.CALLS.value)
        _add_edge(g, "svc_downstream", "tbl_x", EdgeType.WRITES_TO)
        coordinators = detect_saga_coordinators(g)
        assert "svc_one" not in coordinators

    def test_saga_suppress_downstream_findings(self):
        """Race findings between saga steps should be suppressed."""
        from core.graph.rules.race_condition import detect_race_conditions

        g = self._make_saga_graph()
        # Introduce a shared write target to produce a cross-service finding
        _add_edge(g, "svc_step_a", "tbl_payments", EdgeType.WRITES_TO)
        _add_edge(g, "svc_step_b", "tbl_payments", EdgeType.WRITES_TO)

        findings = detect_race_conditions(g)
        assert len(findings) >= 1

        warnings: list[str] = []
        findings = suppress_saga_findings(findings, g, warnings)

        suppressed = [f for f in findings if f.suppressed]
        assert len(suppressed) >= 1, "Saga downstream findings should be suppressed"
        assert any("SAGA" in w or "saga" in w.lower() for w in warnings), \
            "Saga suppression should emit a warning about compensating transactions"

    def test_saga_warning_mentions_compensating_transactions(self):
        """The saga warning must advise about compensating transactions."""
        g = self._make_saga_graph()
        warnings: list[str] = []
        suppress_saga_findings([], g, warnings)
        assert warnings, "Saga detector should emit at least one warning"
        assert any("compensating" in w.lower() for w in warnings), \
            "Warning should mention compensating transactions"


# ─── Full Phase 2 Pipeline Test ────────────────────────────────────────────────

class TestPhase2FullPipeline:

    def test_toctou_found_in_toctou_fixture(self):
        """
        End-to-end: parse toctou_service.py, build graph, detect TOCTOU.
        """
        from core.ingestion.iac_parser import IaCParser
        from core.graph.builder import GraphBuilder

        # Parse IaC to get replica counts
        iac = IaCParser()
        iac_result = iac.parse_directory(FIXTURES / "iac")

        # Parse the TOCTOU fixture
        parser = ASTParser()
        parsed = parser.parse_file(
            PY_FIXTURES / "toctou_service.py",
            service_id="svc_toctou",
        )

        # Build graph
        builder = GraphBuilder()
        builder.add_iac_result(iac_result)
        builder.add_parsed_service(parsed)
        graph = builder.build()

        # Detect
        findings = detect_toctou(graph)

        assert len(findings) >= 1, \
            "End-to-end TOCTOU detection should find at least one finding"

    def test_all_phase2_rules_return_list(self):
        """Smoke test: all Phase 2 rule functions return a list (no crashes)."""
        g = nx.MultiDiGraph()
        _make_service(g, "svc_test", replica_count=1)
        _make_table(g, "tbl_test")

        assert isinstance(detect_toctou(g), list)
        assert isinstance(detect_redis_state_races(g), list)
        assert isinstance(detect_retry_hazards(g), list)
        assert isinstance(detect_saga_coordinators(g), set)

    def test_combined_rules_no_duplicate_findings(self):
        """Running all rules on a simple graph should not produce duplicates."""
        g = nx.MultiDiGraph()
        _make_service(g, "svc_a", replica_count=3)
        _make_service(g, "svc_b", replica_count=3)
        _make_table(g, "tbl_shared")
        _add_edge(g, "svc_a", "tbl_shared", EdgeType.READS_FROM)
        _add_edge(g, "svc_a", "tbl_shared", EdgeType.WRITES_TO)
        _add_edge(g, "svc_b", "tbl_shared", EdgeType.WRITES_TO)

        from core.graph.rules.race_condition import detect_race_conditions
        all_findings = (
            detect_race_conditions(g)
            + detect_toctou(g)
            + detect_redis_state_races(g)
            + detect_retry_hazards(g)
        )

        # No duplicate (actor1, actor2, target) combos for same collision_type
        keys = [(f.collision_type, f.actor_1_id, f.actor_2_id, f.shared_target_id)
                for f in all_findings]
        assert len(keys) == len(set(keys)), "Rules should not produce exact duplicate findings"
