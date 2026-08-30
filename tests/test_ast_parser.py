"""
tests/test_ast_parser.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the AST parser module.
Tests both the tree-sitter path (if available) and the regex fallback.
"""

import pytest
from pathlib import Path

from core.ingestion.ast_parser import ASTParser, parse_service_directory
from core.schema import EdgeType

FIXTURES = Path(__file__).parent / "fixtures"
PY_FIXTURES  = FIXTURES / "vulnerable_python"
JS_FIXTURES  = FIXTURES / "vulnerable_js"


class TestPythonParser:

    def test_detects_django_save_write(self):
        parser = ASTParser()
        result = parser.parse_file(
            PY_FIXTURES / "order_service.py",
            service_id="svc_order",
        )
        write_edges = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        assert len(write_edges) >= 1, (
            "Should detect at least one WRITES_TO edge from Django .save() call"
        )

    def test_detects_sqlalchemy_commit_write(self):
        parser = ASTParser()
        result = parser.parse_file(
            PY_FIXTURES / "inventory_service.py",
            service_id="svc_inventory",
        )
        write_edges = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        assert len(write_edges) >= 1, (
            "Should detect at least one WRITES_TO edge from SQLAlchemy session.commit()"
        )

    def test_detects_reads(self):
        parser = ASTParser()
        result = parser.parse_file(
            PY_FIXTURES / "order_service.py",
            service_id="svc_order",
        )
        read_edges = [e for e in result.edges if e.edge_type == EdgeType.READS_FROM]
        assert len(read_edges) >= 1, (
            "Should detect at least one READS_FROM edge from Django .get() call"
        )

    def test_service_id_set_correctly(self):
        parser = ASTParser()
        result = parser.parse_file(
            PY_FIXTURES / "order_service.py",
            service_id="svc_order_test",
        )
        assert result.service_id == "svc_order_test"
        for edge in result.edges:
            assert edge.source_id == "svc_order_test", (
                "All edges should originate from the given service_id"
            )

    def test_language_detected_as_python(self):
        parser = ASTParser()
        result = parser.parse_file(
            PY_FIXTURES / "order_service.py",
            service_id="svc_order",
        )
        assert result.language == "python"


class TestJavaScriptParser:

    def test_detects_sequelize_save_write(self):
        parser = ASTParser()
        result = parser.parse_file(
            JS_FIXTURES / "paymentController.js",
            service_id="svc_payment",
        )
        write_edges = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        assert len(write_edges) >= 1, (
            "Should detect WRITES_TO from Sequelize .save() or raw SQL INSERT"
        )

    def test_detects_typeorm_save_write(self):
        parser = ASTParser()
        result = parser.parse_file(
            JS_FIXTURES / "notificationService.js",
            service_id="svc_notification",
        )
        write_edges = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        assert len(write_edges) >= 1, (
            "Should detect WRITES_TO from TypeORM .save() call"
        )

    def test_language_detected_as_javascript(self):
        parser = ASTParser()
        result = parser.parse_file(
            JS_FIXTURES / "paymentController.js",
            service_id="svc_payment",
        )
        assert result.language == "javascript"

    def test_raw_sql_insert_detected(self):
        """Verify raw SQL INSERT strings are parsed as WRITES_TO edges."""
        parser = ASTParser()
        result = parser.parse_file(
            JS_FIXTURES / "paymentController.js",
            service_id="svc_payment",
        )
        # Raw SQL write should produce a WRITES_TO edge too
        sql_write_edges = [
            e for e in result.edges
            if e.edge_type == EdgeType.WRITES_TO
            and e.metadata.get("pattern", "").startswith("raw_sql")
        ]
        # Only assert if tree-sitter is available (regex fallback may miss template literals)
        try:
            import tree_sitter_javascript
            assert len(sql_write_edges) >= 1, "Raw SQL INSERT should be detected via tree-sitter"
        except ImportError:
            pytest.skip("tree-sitter-javascript not installed, skipping SQL detection test")


class TestServiceDirectoryParser:

    def test_parse_directory_merges_all_files(self):
        """parse_service_directory should merge edges from all .py files."""
        result = parse_service_directory(
            PY_FIXTURES,
            service_id="svc_merged",
            service_name="merged_service",
            extensions=(".py",),
        )
        write_edges = [e for e in result.edges if e.edge_type == EdgeType.WRITES_TO]
        # Should have writes from BOTH order_service.py AND inventory_service.py
        assert len(write_edges) >= 2, (
            "Directory parse should collect writes from all Python files"
        )
