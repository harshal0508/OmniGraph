"""
core/arbiter/scrubber.py
-----------------------------------------------------------------------------
AST Scrubber — Privacy layer between OmniGraph findings and LLM APIs.

PRIVACY CONTRACT:
  OmniGraph's core promise is that no raw source code or proprietary
  identifiers ever leave the developer's machine unscrubberd.

  Before any LLM call, this module converts a CollisionFinding into a
  fully anonymized structural representation:

    Real:       svc_payment_service  →  Scrubbed: Service_A
    Real:       table_user_wallets   →  Scrubbed: Datastore_1
    Real:       /repo/src/wallet.py  →  Scrubbed: <source_file>

  The scrubbed payload contains ONLY:
    - Collision type (e.g., "TOCTOU Race Condition")
    - Anonymous actor IDs (Service_A, Service_B)
    - Anonymous target ID (Datastore_1)
    - Confidence score
    - Severity
    - Pattern names (e.g., "Django.save()" — these are framework names,
      not user identifiers, so they are safe to include)
    - Replica counts (numerical, not business data)

WHAT IS NEVER SENT:
    - File paths
    - Function names from user code
    - Variable names from user code
    - Table/column names from the actual schema
    - Any string literals from user code
    - Service names that could reveal business logic
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.schema import CollisionFinding, Severity


@dataclass
class ScrubbedFinding:
    """
    A privacy-safe representation of a CollisionFinding, ready for LLM submission.
    Contains zero proprietary identifiers.
    """
    collision_type:    str
    actor_a_label:     str    # e.g. "Service_A"
    actor_b_label:     str    # e.g. "Service_A" (same) or "Service_B" (different)
    target_label:      str    # e.g. "Datastore_1"
    severity:          str
    confidence:        float
    atomic_protection: bool
    replica_count_a:   int
    replica_count_b:   int
    patterns:          list[str]   # ORM/framework pattern names only (no user identifiers)
    remediation_hint:  str         # Our own generated hint — already scrubbed

    def to_prompt_context(self) -> str:
        """
        Render as a structured context block for insertion into an LLM prompt.
        Human-readable, unambiguous, privacy-safe.
        """
        actors = (
            f"Same service ({self.actor_a_label}) on multiple replicas"
            if self.actor_a_label == self.actor_b_label
            else f"{self.actor_a_label} and {self.actor_b_label}"
        )
        protection = "YES — but appears incomplete" if self.atomic_protection else "NO"
        patterns_str = ", ".join(self.patterns) if self.patterns else "unknown pattern"

        return f"""
FINDING TYPE:      {self.collision_type}
SEVERITY:          {self.severity}
CONFIDENCE:        {int(self.confidence * 100)}%

ACTORS:            {actors}
SHARED DATASTORE:  {self.target_label}
ATOMIC PROTECTION: {protection}
REPLICA COUNT:     Service_A={self.replica_count_a}, Service_B={self.replica_count_b}
ORM PATTERNS:      {patterns_str}

CURRENT PARTIAL FIX HINT: {self.remediation_hint}
""".strip()


class ASTScrubber:
    """
    Converts real CollisionFindings into privacy-safe ScrubbedFindings.

    Maintains a session-scoped mapping so that multiple findings in the
    same scan use consistent anonymized labels (Service_A always refers
    to the same real service within one scan session).
    """

    def __init__(self) -> None:
        self._service_map: dict[str, str] = {}
        self._target_map:  dict[str, str] = {}
        self._svc_counter: int = 0
        self._tgt_counter: int = 0

    def _anonymize_service(self, real_id: str) -> str:
        if real_id not in self._service_map:
            label = f"Service_{chr(65 + self._svc_counter)}"  # A, B, C ...
            self._service_map[real_id] = label
            self._svc_counter += 1
        return self._service_map[real_id]

    def _anonymize_target(self, real_id: str) -> str:
        if real_id not in self._target_map:
            label = f"Datastore_{self._tgt_counter + 1}"
            self._target_map[real_id] = label
            self._tgt_counter += 1
        return self._target_map[real_id]

    @staticmethod
    def _extract_patterns(finding: CollisionFinding) -> list[str]:
        """
        Extract ORM pattern names from evidence.
        These are framework names (e.g. "Django.save()") — NOT user identifiers.
        Safe to include in LLM payload.
        """
        patterns = []
        for ev in finding.evidence:
            # Evidence description format: "... [Pattern.name()]"
            desc = ev.description
            start = desc.rfind("[")
            end   = desc.rfind("]")
            if start != -1 and end != -1 and end > start:
                pattern = desc[start + 1:end].strip()
                if pattern and pattern not in patterns:
                    patterns.append(pattern)
        return patterns[:5]   # Cap at 5 to keep prompt concise

    def scrub(self, finding: CollisionFinding, graph=None) -> ScrubbedFinding:
        """
        Convert a real CollisionFinding into a privacy-safe ScrubbedFinding.

        Args:
            finding: The real finding from the detection engine
            graph:   Optional NetworkX graph — used to look up replica counts
        """
        actor_a = self._anonymize_service(finding.actor_1_id)
        actor_b = self._anonymize_service(finding.actor_2_id)
        target  = self._anonymize_target(finding.shared_target_id)

        # Extract replica counts from graph if available
        replica_a = replica_b = 1
        if graph is not None:
            replica_a = graph.nodes.get(finding.actor_1_id, {}).get("replica_count", 1)
            replica_b = graph.nodes.get(finding.actor_2_id, {}).get("replica_count", 1)

        return ScrubbedFinding(
            collision_type    = finding.collision_type,
            actor_a_label     = actor_a,
            actor_b_label     = actor_b,
            target_label      = target,
            severity          = finding.severity.value,
            confidence        = finding.confidence,
            atomic_protection = finding.atomic_protection,
            replica_count_a   = replica_a,
            replica_count_b   = replica_b,
            patterns          = self._extract_patterns(finding),
            remediation_hint  = finding.remediation_hint or "",
        )

    def scrub_all(
        self,
        findings: list[CollisionFinding],
        graph=None,
    ) -> list[tuple[CollisionFinding, ScrubbedFinding]]:
        """Scrub a list of findings. Returns (original, scrubbed) pairs."""
        return [
            (f, self.scrub(f, graph))
            for f in findings
            if f.is_actionable   # Only scrub actionable findings
        ]
