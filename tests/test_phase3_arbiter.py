"""
tests/test_phase3_arbiter.py
-----------------------------------------------------------------------------
Phase 3 Arbiter tests: Privacy scrubber, LLM parsing, and ensemble logic.
"""

from __future__ import annotations

import pytest
import networkx as nx

from core.schema import CollisionFinding, EvidencePath, Severity, NodeType
from core.arbiter.scrubber import ASTScrubber
from core.arbiter.arbiter import OmniGraphArbiter, _judge_responses, ArbiterPatch, get_patch


# ─── AST Scrubber Tests ────────────────────────────────────────────────────────

class TestASTScrubber:

    @pytest.fixture
    def sample_finding(self) -> CollisionFinding:
        return CollisionFinding(
            collision_type="Distributed Race Condition",
            actor_1_id="users_microservice_dev",
            actor_2_id="billing_engine_v2",
            shared_target_id="rds_postgres_cluster_xyz",
            atomic_protection=False,
            confidence=0.90,
            evidence=[
                EvidencePath("src/users/db.py", 42, "READ from 'users' [SQLAlchemy.query()]"),
                EvidencePath("src/billing/db.py", 12, "WRITE to 'users' [Django.save()]"),
            ],
            severity=Severity.CRITICAL,
        )

    def test_scrubber_anonymizes_identifiers(self, sample_finding: CollisionFinding):
        scrubber = ASTScrubber()
        scrubbed = scrubber.scrub(sample_finding)

        # Real IDs should be gone
        assert "users_microservice" not in scrubbed.actor_a_label
        assert "billing_engine" not in scrubbed.actor_b_label
        assert "rds_postgres" not in scrubbed.target_label

        # Replaced with generic labels
        assert scrubbed.actor_a_label == "Service_A"
        assert scrubbed.actor_b_label == "Service_B"
        assert scrubbed.target_label == "Datastore_1"

    def test_scrubber_preserves_consistent_labels(self, sample_finding: CollisionFinding):
        scrubber = ASTScrubber()
        f1 = scrubber.scrub(sample_finding)
        
        # Second finding with same actors should get same labels
        f2 = scrubber.scrub(sample_finding)
        
        assert f1.actor_a_label == f2.actor_a_label
        assert f1.target_label == f2.target_label

    def test_scrubber_extracts_framework_patterns(self, sample_finding: CollisionFinding):
        scrubber = ASTScrubber()
        scrubbed = scrubber.scrub(sample_finding)
        
        assert "SQLAlchemy.query()" in scrubbed.patterns
        assert "Django.save()" in scrubbed.patterns

    def test_scrubber_fetches_replica_counts_from_graph(self, sample_finding: CollisionFinding):
        g = nx.MultiDiGraph()
        g.add_node("users_microservice_dev", node_type=NodeType.SERVICE.value, replica_count=5)
        g.add_node("billing_engine_v2", node_type=NodeType.SERVICE.value, replica_count=2)
        
        scrubber = ASTScrubber()
        scrubbed = scrubber.scrub(sample_finding, graph=g)
        
        assert scrubbed.replica_count_a == 5
        assert scrubbed.replica_count_b == 2


# ─── Ensemble Judge Tests ──────────────────────────────────────────────────────

class TestEnsembleJudge:

    def test_judge_single_response(self):
        responses = [("claude-3-5-sonnet", {"is_genuine_race": True, "arbiter_confidence": "HIGH"})]
        best = _judge_responses(responses)
        assert best is not None
        assert best[0] == "claude-3-5-sonnet"

    def test_judge_majority_vote(self):
        responses = [
            ("claude-3-5-sonnet", {"is_genuine_race": True}),
            ("gemini-1.5-pro", {"is_genuine_race": False}),
            ("gpt-4o", {"is_genuine_race": True}),
        ]
        best = _judge_responses(responses)
        assert best is not None
        # Should pick one of the True votes
        assert best[1]["is_genuine_race"] is True
        
    def test_judge_confidence_tiebreak(self):
        responses = [
            ("gemini-1.5-pro", {"is_genuine_race": True, "arbiter_confidence": "HIGH"}),
            ("gpt-4o", {"is_genuine_race": True, "arbiter_confidence": "LOW"}),
        ]
        best = _judge_responses(responses)
        assert best is not None
        assert best[0] == "gemini-1.5-pro"  # Higher confidence wins


# ─── Arbiter Pipeline Mock Test ────────────────────────────────────────────────

class MockLLMClient:
    def __init__(self):
        self.is_online = True
        self.available_providers = ["mock-claude", "mock-gemini"]

    def call_ensemble(self, scrubbed):
        return [
            ("mock-claude", {
                "is_genuine_race": True,
                "root_cause": "Test cause",
                "fix_recommendation": "Use SELECT FOR UPDATE",
                "fix_pattern": "SELECT_FOR_UPDATE",
                "arbiter_confidence": "HIGH"
            })
        ]

class TestArbiterOrchestrator:

    def test_arbiter_enriches_findings_in_place(self, monkeypatch):
        # Setup finding
        finding = CollisionFinding(
            collision_type="Test",
            actor_1_id="A",
            actor_2_id="B",
            shared_target_id="T",
            atomic_protection=False,
            confidence=0.9,
            evidence=[],
            severity=Severity.CRITICAL,
        )

        arbiter = OmniGraphArbiter()
        # Inject mock client
        arbiter.client = MockLLMClient()
        
        result = arbiter.enrich([finding])
        
        # Original finding should be returned and enriched
        assert len(result) == 1
        assert result[0] is finding
        
        patch = get_patch(result[0])
        assert patch is not None
        assert isinstance(patch, ArbiterPatch)
        assert patch.is_genuine_race is True
        assert patch.fix_pattern == "SELECT_FOR_UPDATE"
        assert patch.provider == "mock-claude"
