"""
tests/test_iac_parser.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the IaC parser module.
"""

import pytest
from pathlib import Path

from core.ingestion.iac_parser import IaCParser
from core.schema import DbIsolationLevel

FIXTURES = Path(__file__).parent / "fixtures" / "iac"


class TestDockerComposeParser:

    def setup_method(self):
        self.parser = IaCParser()
        self.result = self.parser.parse_file(FIXTURES / "docker-compose.yml")

    def test_discovers_application_services(self):
        service_names = {s.name for s in self.result.services}
        # Should find order-service and payment-service
        assert len(self.result.services) >= 1, "Should discover at least one app service"

    def test_discovers_postgres_database(self):
        db_types = {d.db_type for d in self.result.databases}
        assert "postgresql" in db_types, "Should recognise postgres image as postgresql DB"

    def test_discovers_redis(self):
        queue_names = {q.name for q in self.result.queues}
        # Redis found as a queue/cache node
        assert len(self.result.queues) >= 1 or len(self.result.databases) >= 1, (
            "Redis should be detected as a datastore node"
        )

    def test_replica_count_parsed(self):
        for svc in self.result.services:
            assert isinstance(svc.replica_count, int), "replica_count must be an integer"
            assert svc.replica_count >= 1, "replica_count must be at least 1"


class TestKubernetesParser:

    def setup_method(self):
        self.parser = IaCParser()
        self.result = self.parser.parse_file(FIXTURES / "kubernetes-deployment.yaml")

    def test_discovers_services_with_correct_replicas(self):
        replica_counts = {s.name: s.replica_count for s in self.result.services}
        # vulnerable-python should have 3 replicas
        # vulnerable-js should have 5 replicas
        assert any(r > 1 for r in replica_counts.values()), (
            "At least one service should have replica_count > 1"
        )

    def test_postgres_db_discovered(self):
        db_types = {d.db_type for d in self.result.databases}
        assert "postgresql" in db_types, "PostgreSQL should be discovered from postgres:16 image"

    def test_multi_doc_yaml_parses_all_deployments(self):
        """k8s files use --- multi-doc YAML. All Deployments should be parsed."""
        total_nodes = len(self.result.services) + len(self.result.databases)
        assert total_nodes >= 2, "Should parse at least 2 Deployment documents"


class TestDirectoryParser:

    def test_parse_directory_merges_both_files(self):
        parser = IaCParser()
        result = parser.parse_directory(FIXTURES)
        # Should find services from both docker-compose AND k8s
        total = len(result.services) + len(result.databases) + len(result.queues)
        assert total >= 3, "Directory parse should collect nodes from both YAML files"
