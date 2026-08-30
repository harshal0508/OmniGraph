"""
tests/test_race_detection.py
─────────────────────────────────────────────────────────────────────────────
End-to-end integration tests for the full OmniGraph detection pipeline.

These tests run the complete pipeline:
  IaC Parse → AST Parse → Graph Build → Race Detection → Suppression

and verify that the expected findings are produced with correct severity,
confidence, and suppression behaviour.
"""

import pytest
from pathlib import Path

import networkx as nx

from core.ingestion.ast_parser import ASTParser
from core.ingestion.iac_parser import IaCParser
from core.graph.builder import GraphBuilder
from core.graph.rules.race_condition import detect_race_conditions
from core.graph.rules.suppressors import apply_all_suppressors
from core.schema import (
    Severity, EdgeType, NodeType,
    ServiceNode, DatabaseNode, TableNode, GraphEdge,
)
from core.ingestion.ast_parser import ParsedService

FIXTURES     = Path(__file__).parent / "fixtures"
PY_FIXTURES  = FIXTURES / "vulnerable_python"
JS_FIXTURES  = FIXTURES / "vulnerable_js"
IAC_FIXTURES = FIXTURES / "iac"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_minimal_graph(
    service_a_id: str,
    service_b_id: str,
    replica_a: int = 3,
    replica_b: int = 3,
    table_id: str = "table_shared",
    add_lock_to_a: bool = False,
    add_lock_to_b: bool = False,
    add_txn_to_a: bool = False,
    add_txn_to_b: bool = False,
) -> nx.MultiDiGraph:
    """
    Build a minimal graph with two services both writing to one table.
    Used for targeted unit tests of individual rules.
    """
    builder = GraphBuilder()

    # Register services via IaC
    iac_result = type("IaC", (), {
        "services": [
            ServiceNode(service_a_id, "Service A", "python", replica_a),
            ServiceNode(service_b_id, "Service B", "javascript", replica_b),
        ],
        "databases": [DatabaseNode("db_main", "main_db", "postgresql")],
        "queues": [],
        "edges": [],
        "warnings": [],
    })()
    builder.add_iac_result(iac_result)

    # Add the shared table manually
    graph = builder.build()
    graph.add_node(table_id, node_type=NodeType.TABLE.value, name="shared_table", database_id="db_main")

    # Add WRITES_TO edges
    graph.add_edge(service_a_id, table_id,
                   edge_type=EdgeType.WRITES_TO.value,
                   source_file="service_a.py", source_line=42, pattern="Django.save()")
    graph.add_edge(service_b_id, table_id,
                   edge_type=EdgeType.WRITES_TO.value,
                   source_file="service_b.js", source_line=17, pattern="Sequelize.save()")

    # Optionally add lock nodes
    if add_lock_to_a:
        graph.add_node("mutex_a", node_type=NodeType.MUTEX.value, name="lock_a", mutex_type="redis")
        graph.add_edge(service_a_id, "mutex_a", edge_type=EdgeType.USES_LOCK.value)

    if add_lock_to_b:
        graph.add_node("mutex_b", node_type=NodeType.MUTEX.value, name="lock_b", mutex_type="redis")
        graph.add_edge(service_b_id, "mutex_b", edge_type=EdgeType.USES_LOCK.value)

    if add_txn_to_a:
        graph.nodes[service_a_id]["has_transaction"] = True
    if add_txn_to_b:
        graph.nodes[service_b_id]["has_transaction"] = True

    return graph


# ─── Core Detection Tests ─────────────────────────────────────────────────────

class TestRaceConditionDetection:

    def test_detects_unprotected_concurrent_writes(self):
        """Two services with replicas > 1 writing to same table → CRITICAL finding."""
        graph = _build_minimal_graph("svc_a", "svc_b", replica_a=3, replica_b=5)
        findings = detect_race_conditions(graph)
        assert len(findings) >= 1, "Should detect at least one collision"
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) >= 1, "Finding should be CRITICAL for multi-replica services"

    def test_no_finding_for_fully_locked_services(self):
        """Both services use a distributed lock → no CRITICAL finding."""
        graph = _build_minimal_graph(
            "svc_a", "svc_b",
            add_lock_to_a=True,
            add_lock_to_b=True,
        )
        findings = detect_race_conditions(graph)
        # Either no findings, or all are suppressed / INFO
        actionable = [f for f in findings if f.is_actionable and f.severity == Severity.CRITICAL]
        assert len(actionable) == 0, (
            "Fully locked services should not produce a CRITICAL actionable finding"
        )

    def test_partial_lock_still_produces_finding(self):
        """Only one service has a lock → still a race (half-protected)."""
        graph = _build_minimal_graph("svc_a", "svc_b", add_lock_to_a=True)
        findings = detect_race_conditions(graph)
        assert len(findings) >= 1, (
            "Partial lock (only one service) still produces a collision finding"
        )

    def test_transaction_protection_reduces_severity(self):
        """Both services use transactions → finding confidence drops."""
        graph = _build_minimal_graph("svc_a", "svc_b", add_txn_to_a=True, add_txn_to_b=True)
        findings = detect_race_conditions(graph)
        if findings:
            assert findings[0].confidence < 1.0, (
                "Transaction protection should reduce confidence score"
            )

    def test_single_writer_produces_no_finding(self):
        """Only one service writes to the table → no cross-service collision."""
        builder = GraphBuilder()
        iac_result = type("IaC", (), {
            "services": [ServiceNode("svc_only", "Only Service", "python", 3)],
            "databases": [DatabaseNode("db_main", "main_db", "postgresql")],
            "queues": [], "edges": [], "warnings": [],
        })()
        builder.add_iac_result(iac_result)
        graph = builder.build()
        graph.add_node("table_x", node_type=NodeType.TABLE.value, name="table_x", database_id="db_main")
        graph.add_edge("svc_only", "table_x",
                       edge_type=EdgeType.WRITES_TO.value, source_file="only.py", source_line=1)

        findings = detect_race_conditions(graph)
        assert len(findings) == 0, "Single writer → zero collision findings"

    def test_confidence_score_range(self):
        """All confidence scores must be in [0.0, 1.0]."""
        graph = _build_minimal_graph("svc_a", "svc_b")
        findings = detect_race_conditions(graph)
        for f in findings:
            assert 0.0 <= f.confidence <= 1.0, f"Confidence {f.confidence} out of range"

    def test_finding_contains_evidence(self):
        """Every finding must have at least one EvidencePath."""
        graph = _build_minimal_graph("svc_a", "svc_b")
        findings = detect_race_conditions(graph)
        for f in findings:
            assert len(f.evidence) >= 1, "Finding must contain evidence path(s)"

    def test_finding_contains_remediation_hint(self):
        """Every actionable finding must include a remediation hint."""
        graph = _build_minimal_graph("svc_a", "svc_b", replica_a=5, replica_b=5)
        findings = detect_race_conditions(graph)
        for f in findings:
            if f.is_actionable:
                assert f.remediation_hint, "Actionable finding must include a remediation hint"


# ─── Suppressor Tests ─────────────────────────────────────────────────────────

class TestSuppressionRules:

    def test_serializable_isolation_suppresses_finding(self):
        """EC-1: SERIALIZABLE DB isolation suppresses the race finding."""
        from core.schema import DbIsolationLevel
        graph = _build_minimal_graph("svc_a", "svc_b")

        # Inject SERIALIZABLE isolation on the database
        graph.nodes["db_main"]["isolation_level"] = DbIsolationLevel.SERIALIZABLE.value
        graph.nodes["table_shared"]["database_id"] = "db_main"

        findings = detect_race_conditions(graph)
        warnings: list[str] = []
        findings = apply_all_suppressors(findings, graph, warnings)

        suppressed = [f for f in findings if f.suppressed]
        assert len(suppressed) >= 1, (
            "SERIALIZABLE isolation should suppress the race finding (EC-1)"
        )

    def test_single_replica_downgrades_to_warning(self):
        """EC-4: Both single-replica services → downgrade CRITICAL to WARNING."""
        graph = _build_minimal_graph("svc_a", "svc_b", replica_a=1, replica_b=1)
        findings = detect_race_conditions(graph)
        warnings: list[str] = []
        findings = apply_all_suppressors(findings, graph, warnings)

        critical = [f for f in findings if f.severity == Severity.CRITICAL and f.is_actionable]
        assert len(critical) == 0, (
            "Both single-replica services should not produce CRITICAL findings (EC-4)"
        )

    def test_scrubbed_dict_has_no_source_paths(self):
        """Privacy: scrubbed dict must not contain raw file paths."""
        graph = _build_minimal_graph("svc_a", "svc_b")
        findings = detect_race_conditions(graph)
        assert len(findings) >= 1

        scrubbed = findings[0].to_scrubbed_dict()
        scrubbed_str = str(scrubbed)
        # Ensure no file paths appear in the scrubbed output
        assert ".py" not in scrubbed_str, "Scrubbed dict must not contain .py file paths"
        assert ".js" not in scrubbed_str, "Scrubbed dict must not contain .js file paths"
        assert "/" not in scrubbed_str and "\\" not in scrubbed_str, (
            "Scrubbed dict must not contain directory paths"
        )


# ─── Full Pipeline Integration Test ───────────────────────────────────────────

class TestFullPipeline:

    def test_end_to_end_with_fixtures(self):
        """
        Full pipeline: IaC + Python fixtures → detect race conditions.
        This is the most important test — it proves the system works end-to-end.
        """
        # Step 1: Parse IaC
        iac_parser = IaCParser()
        iac_result = iac_parser.parse_directory(IAC_FIXTURES)

        # Step 2: Parse Python source
        ast_parser = ASTParser()
        parsed_py = ast_parser.parse_file(
            PY_FIXTURES / "order_service.py",
            service_id="svc_vulnerable_python",
        )
        parsed_py2 = ast_parser.parse_file(
            PY_FIXTURES / "inventory_service.py",
            service_id="svc_inventory_service",
        )

        # Step 3: Build graph
        builder = GraphBuilder()
        builder.add_iac_result(iac_result)
        builder.add_parsed_service(parsed_py)
        builder.add_parsed_service(parsed_py2)
        graph = builder.build()

        # Step 4: Detect
        findings = detect_race_conditions(graph)

        # Step 5: Suppress
        warnings: list[str] = []
        findings = apply_all_suppressors(findings, graph, warnings)

        # Assertions
        assert len(findings) >= 1, (
            "End-to-end pipeline should detect at least one race condition "
            "from the vulnerable Python fixtures"
        )

        actionable = [f for f in findings if f.is_actionable]
        assert len(actionable) >= 1, "At least one finding must be actionable (not suppressed)"

        # Every finding should have actor IDs
        for f in actionable:
            assert f.actor_1_id, "actor_1_id must be set"
            assert f.actor_2_id, "actor_2_id must be set"
            assert f.shared_target_id, "shared_target_id must be set"
