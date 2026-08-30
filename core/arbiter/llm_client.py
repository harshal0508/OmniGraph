"""
core/arbiter/llm_client.py
-----------------------------------------------------------------------------
LLM API Client — Bring Your Own Key (BYOK) implementation.

SUPPORTED PROVIDERS  (set one env var to activate):
  ANTHROPIC_API_KEY   → Claude 3.5 Sonnet
  GOOGLE_API_KEY      → Gemini 1.5 Pro
  OPENAI_API_KEY      → GPT-4o (optional third arbiter)

PRIVACY:
  This client ONLY receives ScrubbedFinding payloads from the ASTScrubber.
  It never touches raw source code or real identifier names.

OFFLINE MODE:
  If NO env vars are set, all methods return None immediately.
  The rest of the pipeline handles None gracefully and skips LLM sections.
"""

from __future__ import annotations

import os
import json
import textwrap
from typing import Optional

from core.arbiter.scrubber import ScrubbedFinding


# ─── Prompt Templates ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""
You are OmniGraph Arbiter, a senior distributed-systems engineer specializing
in concurrency correctness and database consistency.

You receive anonymized descriptions of race conditions found by a static
analysis engine. Your job is to:

1. Confirm whether the finding is a genuine race condition.
2. Explain the root cause in 2-3 sentences (plain English, no code).
3. Provide a concrete fix recommendation — name the specific API or pattern
   (e.g. SELECT FOR UPDATE, Redis Redlock, database advisory lock, saga + compensating tx).
4. Rate your confidence in the fix: HIGH / MEDIUM / LOW.

CONSTRAINTS:
- Do NOT ask for more information.
- Do NOT write actual code — describe the fix pattern precisely.
- Keep total response under 300 words.
- Respond ONLY in valid JSON matching this schema:
  {
    "is_genuine_race": true,
    "root_cause": "...",
    "fix_recommendation": "...",
    "fix_pattern": "SELECT_FOR_UPDATE | DISTRIBUTED_LOCK | SAGA | UPSERT | ATOMIC_OP | OTHER",
    "arbiter_confidence": "HIGH | MEDIUM | LOW",
    "additional_context": "..." (optional, null if none)
  }
""").strip()


def _build_user_prompt(scrubbed: ScrubbedFinding) -> str:
    return f"""
Analyze this distributed race condition finding and provide your assessment.

--- FINDING ---
{scrubbed.to_prompt_context()}
--- END FINDING ---

Respond with valid JSON only.
""".strip()


# ─── Provider Implementations ─────────────────────────────────────────────────

def _call_anthropic(scrubbed: ScrubbedFinding, api_key: str) -> Optional[dict]:
    """Call Claude 3.5 Sonnet via Anthropic SDK."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(scrubbed)}],
        )
        raw = message.content[0].text.strip()
        return json.loads(raw)
    except ImportError:
        return None
    except Exception:
        return None


def _call_gemini(scrubbed: ScrubbedFinding, api_key: str) -> Optional[dict]:
    """Call Gemini 1.5 Pro via Google GenerativeAI SDK."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            system_instruction=_SYSTEM_PROMPT,
        )
        response = model.generate_content(
            _build_user_prompt(scrubbed),
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=512,
                temperature=0.2,
            ),
        )
        return json.loads(response.text)
    except ImportError:
        return None
    except Exception:
        return None


def _call_openai(scrubbed: ScrubbedFinding, api_key: str) -> Optional[dict]:
    """Call GPT-4o via OpenAI SDK (used as arbiter judge)."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            max_tokens=512,
            temperature=0.1,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": _build_user_prompt(scrubbed)},
            ],
        )
        raw = response.choices[0].message.content
        return json.loads(raw)
    except ImportError:
        return None
    except Exception:
        return None


# ─── Public Interface ──────────────────────────────────────────────────────────

class LLMClient:
    """
    Unified LLM client. Automatically selects available provider(s) from
    environment variables. Falls back to offline mode if none configured.
    """

    def __init__(self) -> None:
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.google_key    = os.environ.get("GOOGLE_API_KEY", "")
        self.openai_key    = os.environ.get("OPENAI_API_KEY", "")

    @property
    def is_online(self) -> bool:
        """True if at least one LLM provider is configured."""
        return bool(self.anthropic_key or self.google_key or self.openai_key)

    @property
    def available_providers(self) -> list[str]:
        providers = []
        if self.anthropic_key: providers.append("claude-3-5-sonnet")
        if self.google_key:    providers.append("gemini-1.5-pro")
        if self.openai_key:    providers.append("gpt-4o")
        return providers

    def call(self, scrubbed: ScrubbedFinding, provider: str = "auto") -> Optional[dict]:
        """
        Call an LLM with a scrubbed finding.

        Args:
            scrubbed:  Privacy-safe finding from ASTScrubber
            provider:  "auto" | "claude" | "gemini" | "openai"

        Returns:
            Parsed JSON response dict, or None if offline / error.
        """
        if not self.is_online:
            return None

        if provider == "auto":
            # Priority: Claude > Gemini > OpenAI
            if self.anthropic_key:
                return _call_anthropic(scrubbed, self.anthropic_key)
            if self.google_key:
                return _call_gemini(scrubbed, self.google_key)
            if self.openai_key:
                return _call_openai(scrubbed, self.openai_key)

        if provider == "claude" and self.anthropic_key:
            return _call_anthropic(scrubbed, self.anthropic_key)
        if provider == "gemini" and self.google_key:
            return _call_gemini(scrubbed, self.google_key)
        if provider == "openai" and self.openai_key:
            return _call_openai(scrubbed, self.openai_key)

        return None

    def call_ensemble(
        self,
        scrubbed: ScrubbedFinding,
    ) -> list[tuple[str, dict]]:
        """
        Call ALL available providers in parallel and return all responses.
        Used when multiple keys are set — the Arbiter then picks the best.

        Returns: list of (provider_name, response_dict) tuples
        """
        results = []
        if self.anthropic_key:
            r = _call_anthropic(scrubbed, self.anthropic_key)
            if r: results.append(("claude-3-5-sonnet", r))
        if self.google_key:
            r = _call_gemini(scrubbed, self.google_key)
            if r: results.append(("gemini-1.5-pro", r))
        if self.openai_key:
            r = _call_openai(scrubbed, self.openai_key)
            if r: results.append(("gpt-4o", r))
        return results
