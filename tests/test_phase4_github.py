"""
tests/test_phase4_github.py
-----------------------------------------------------------------------------
Tests for Phase 4 GitHub PR reporting and Markdown generation.
"""

from __future__ import annotations

import pytest

from core.schema import CollisionFinding, EvidencePath, Severity, AnalysisReport
from core.reporter.github_reporter import generate_markdown_report, _COMMENT_MARKER
from core.arbiter.arbiter import ArbiterPatch


@pytest.fixture
def sample_report() -> AnalysisReport:
    f1 = CollisionFinding(
        collision_type="TOCTOU Race Condition",
        actor_1_id="inventory_svc",
        actor_2_id="inventory_svc",
        shared_target_id="db_main",
        atomic_protection=False,
        confidence=0.85,
        evidence=[
            EvidencePath("inv.py", 10, "READ"),
            EvidencePath("inv.py", 15, "WRITE"),
        ],
        severity=Severity.CRITICAL,
    )
    
    # Attach a mock LLM patch to finding 1
    patch = ArbiterPatch(
        is_genuine_race=True,
        root_cause="Missing row-level lock",
        fix_recommendation="Use SELECT FOR UPDATE",
        fix_pattern="SELECT_FOR_UPDATE",
        arbiter_confidence="HIGH",
        additional_context=None,
        provider="claude-3-5-sonnet",
        was_ensemble=True,
    )
    f1.llm_patch = patch

    f2 = CollisionFinding(
        collision_type="Retry Hazard",
        actor_1_id="billing_svc",
        actor_2_id="billing_svc",
        shared_target_id="db_billing",
        atomic_protection=False,
        confidence=0.65,
        evidence=[EvidencePath("bill.py", 20, "INSERT")],
        severity=Severity.WARNING,
        remediation_hint="Add idempotency key",
    )
    
    f3_suppressed = CollisionFinding(
        collision_type="Race Condition",
        actor_1_id="a",
        actor_2_id="b",
        shared_target_id="x",
        atomic_protection=False,
        confidence=0.1,
        evidence=[],
        severity=Severity.INFO,
        suppressed=True,
        suppression_reason="Single replica",
    )

    return AnalysisReport(
        scan_id="test-123",
        source_path="/test",
        total_services=5,
        total_edges=20,
        findings=[f1, f2, f3_suppressed],
        warnings=[],
    )


class TestGitHubMarkdownGenerator:

    def test_markdown_includes_marker(self, sample_report):
        md = generate_markdown_report(sample_report)
        assert md.startswith(_COMMENT_MARKER)

    def test_markdown_renders_ai_patch(self, sample_report):
        md = generate_markdown_report(sample_report)
        
        # Check that LLM patch details are present
        assert "🤖 AI Arbiter Analysis" in md
        assert "claude-3-5-sonnet" in md
        assert "*(Ensemble Verified)*" in md
        assert "Missing row-level lock" in md
        assert "Use SELECT FOR UPDATE" in md

    def test_markdown_renders_fallback_hint(self, sample_report):
        md = generate_markdown_report(sample_report)
        
        # Finding 2 doesn't have an AI patch, but has a hint
        assert "**Suggested Fix:** Add idempotency key" in md

    def test_markdown_hides_suppressed_details(self, sample_report):
        md = generate_markdown_report(sample_report)
        
        # The suppressed finding should just be counted in the footer, not rendered
        assert "1 findings were automatically suppressed" in md
        assert "Single replica" not in md

    def test_markdown_renders_pass_when_no_actionable(self):
        clean_report = AnalysisReport(
            scan_id="clean",
            source_path="/test",
            total_services=1,
            total_edges=1,
            findings=[],
            warnings=[],
        )
        md = generate_markdown_report(clean_report)
        assert "✅ **PASS**" in md
        assert "FAIL" not in md
