"""
core/arbiter/arbiter.py
-----------------------------------------------------------------------------
OmniGraph Arbiter — Orchestrates the full LLM remediation pipeline.

FLOW:
  CollisionFindings
      → ASTScrubber.scrub_all()    [privacy layer]
      → LLMClient.call_ensemble()  [1-3 LLM providers]
      → _judge_responses()         [pick best / majority vote]
      → attach patch to finding    [enriches finding in-place]

The arbiter enriches findings in-place: it adds a `llm_patch` attribute
containing the LLM-generated structured remediation.

OFFLINE MODE:
  If no API keys are present, enrich() returns immediately with findings
  unchanged. The reporter shows a "LLM arbiter offline" note instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from core.schema import CollisionFinding, Severity
from core.arbiter.scrubber import ASTScrubber, ScrubbedFinding
from core.arbiter.llm_client import LLMClient


# ─── Arbiter Patch Schema ─────────────────────────────────────────────────────

@dataclass
class ArbiterPatch:
    """
    The structured output of the LLM remediation arbiter for one finding.
    Attached to CollisionFinding as an extra attribute by enrich().
    """
    is_genuine_race:    bool
    root_cause:         str
    fix_recommendation: str
    fix_pattern:        str    # SELECT_FOR_UPDATE | DISTRIBUTED_LOCK | SAGA | UPSERT | OTHER
    arbiter_confidence: str    # HIGH | MEDIUM | LOW
    additional_context: Optional[str]
    provider:           str    # Which LLM produced this
    was_ensemble:       bool   # True if multiple providers agreed


# ─── Ensemble Judge ───────────────────────────────────────────────────────────

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _judge_responses(
    responses: list[tuple[str, dict]],
) -> Optional[tuple[str, dict]]:
    """
    Select the best response from the ensemble.

    Strategy (prototype):
      1. If only one response → use it directly
      2. If multiple → prefer unanimous is_genuine_race agreement
      3. Among agreeing responses → pick highest arbiter_confidence
      4. Tiebreak → prefer Claude > Gemini > GPT-4o (order of proven reliability)

    Returns (provider_name, response_dict) or None.
    """
    if not responses:
        return None
    if len(responses) == 1:
        return responses[0]

    # Check unanimous agreement on is_genuine_race
    genuine_votes = [r[1].get("is_genuine_race", True) for r in responses]
    majority_genuine = sum(genuine_votes) > len(genuine_votes) / 2

    # Filter to matching votes
    agreeing = [r for r in responses if r[1].get("is_genuine_race", True) == majority_genuine]
    if not agreeing:
        agreeing = responses

    # Sort by confidence level (descending)
    provider_priority = {"claude-3-5-sonnet": 3, "gemini-1.5-pro": 2, "gpt-4o": 1}
    agreeing.sort(
        key=lambda r: (
            _CONFIDENCE_RANK.get(r[1].get("arbiter_confidence", "LOW"), 0),
            provider_priority.get(r[0], 0),
        ),
        reverse=True,
    )
    return agreeing[0]


def _parse_patch(
    provider: str,
    response: dict,
    was_ensemble: bool,
) -> ArbiterPatch:
    return ArbiterPatch(
        is_genuine_race    = response.get("is_genuine_race", True),
        root_cause         = response.get("root_cause", ""),
        fix_recommendation = response.get("fix_recommendation", ""),
        fix_pattern        = response.get("fix_pattern", "OTHER"),
        arbiter_confidence = response.get("arbiter_confidence", "LOW"),
        additional_context = response.get("additional_context"),
        provider           = provider,
        was_ensemble       = was_ensemble,
    )


# ─── Main Arbiter ─────────────────────────────────────────────────────────────

class OmniGraphArbiter:
    """
    Top-level orchestrator for Phase 3 LLM remediation.

    Usage:
        arbiter = OmniGraphArbiter()
        if arbiter.is_online:
            enriched_findings = arbiter.enrich(findings, graph)
    """

    def __init__(self) -> None:
        self.scrubber = ASTScrubber()
        self.client   = LLMClient()

    @property
    def is_online(self) -> bool:
        return self.client.is_online

    @property
    def providers(self) -> list[str]:
        return self.client.available_providers

    def enrich(
        self,
        findings: list[CollisionFinding],
        graph=None,
        max_findings: int = 5,
    ) -> list[CollisionFinding]:
        """
        Enrich actionable findings with LLM-generated patches.

        Args:
            findings:      Output from detect_* + suppress_* pipeline
            graph:         NetworkX graph (for replica count extraction)
            max_findings:  Cap on LLM calls per scan (cost control)

        Returns:
            Same findings list, with `llm_patch` attribute set on enriched ones.
        """
        if not self.is_online:
            return findings

        actionable = [f for f in findings if f.is_actionable]
        # Process highest-severity findings first, capped at max_findings
        actionable.sort(
            key=lambda f: (
                0 if f.severity == Severity.CRITICAL else 1,
                -f.confidence,
            ),
        )
        to_enrich = actionable[:max_findings]

        for finding in to_enrich:
            try:
                scrubbed  = self.scrubber.scrub(finding, graph)
                responses = self.client.call_ensemble(scrubbed)

                if not responses:
                    continue

                was_ensemble = len(responses) > 1
                best = _judge_responses(responses)
                if best:
                    provider, response = best
                    patch = _parse_patch(provider, response, was_ensemble)
                    # Attach patch as a dynamic attribute
                    finding.llm_patch = patch  # type: ignore[attr-defined]
            except Exception:
                # Never crash the scan because of an LLM failure
                continue

        return findings


# ─── Convenience accessor ─────────────────────────────────────────────────────

def get_patch(finding: CollisionFinding) -> Optional[ArbiterPatch]:
    """Safe accessor — returns None if no LLM patch was attached."""
    return getattr(finding, "llm_patch", None)
