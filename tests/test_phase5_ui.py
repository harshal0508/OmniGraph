"""
tests/test_phase5_ui.py
-----------------------------------------------------------------------------
Tests for Phase 5 UI Exporter.
"""

from __future__ import annotations

import pytest
import json
import networkx as nx
from pathlib import Path

from core.schema import CollisionFinding, EvidencePath, Severity, AnalysisReport, NodeType
from core.reporter.ui_exporter import export_html_dashboard, _graph_to_cytoscape, _findings_to_dicts


@pytest.fixture
def sample_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_node("svc_a", node_type=NodeType.SERVICE.value, name="Service A")
    g.add_node("db_1", node_type=NodeType.DATABASE.value, name="Database 1")
    g.add_edge("svc_a", "db_1", key=0, edge_type="WRITES_TO", pattern="SQLAlchemy")
    return g

@pytest.fixture
def sample_report() -> AnalysisReport:
    f1 = CollisionFinding(
        collision_type="Race Condition",
        actor_1_id="svc_a",
        actor_2_id="svc_a",
        shared_target_id="db_1",
        atomic_protection=False,
        confidence=0.9,
        evidence=[],
        severity=Severity.CRITICAL,
    )
    return AnalysisReport(
        scan_id="test-ui",
        source_path="/test",
        total_services=1,
        total_edges=1,
        findings=[f1],
        warnings=[],
    )


class TestUIExporter:

    def test_graph_conversion(self, sample_graph):
        elements = _graph_to_cytoscape(sample_graph)
        
        nodes = [e for e in elements if "source" not in e["data"]]
        edges = [e for e in elements if "source" in e["data"]]
        
        assert len(nodes) == 2
        assert len(edges) == 1
        assert nodes[0]["data"]["id"] == "svc_a"
        assert edges[0]["data"]["label"] == "WRITES_TO"

    def test_findings_serialization(self, sample_report):
        data = _findings_to_dicts(sample_report)
        assert data["scan_id"] == "test-ui"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["collision_type"] == "Race Condition"

    def test_html_export_creates_file_with_injected_payload(self, tmp_path, sample_report, sample_graph):
        out_file = tmp_path / "report.html"
        export_html_dashboard(sample_report, sample_graph, out_file)
        
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        
        # Check standard HTML shell
        assert "<html>" in content or "<html" in content
        assert "OmniGraph" in content
        
        # Check that payload was injected
        assert "const PAYLOAD =" in content
        assert "test-ui" in content  # Scan ID injected
        assert "svc_a" in content
