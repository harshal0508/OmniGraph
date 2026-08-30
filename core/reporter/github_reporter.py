"""
core/reporter/github_reporter.py
-----------------------------------------------------------------------------
Phase 4: GitHub PR Comment formatter and API integration.

Posts a professional, formatted markdown report directly to a GitHub Pull Request.
It uses a hidden HTML marker to find its previous comments and updates them
in-place to avoid spamming the PR timeline.
"""

from __future__ import annotations

import json
import os
import requests
from typing import Optional

from core.schema import AnalysisReport, CollisionFinding, Severity
from core.arbiter.arbiter import get_patch

# Hidden marker to identify OmniGraph comments
_COMMENT_MARKER = "<!-- omnigraph-report-marker -->"


def _format_finding(idx: int, finding: CollisionFinding) -> str:
    """Format a single finding as a Markdown section."""
    sev_emoji = "🚨" if finding.severity == Severity.CRITICAL else "⚠️"
    
    # Extract AI patch if present
    patch = get_patch(finding)
    fix_section = ""
    
    if patch and not finding.suppressed:
        ensemble_tag = " *(Ensemble Verified)*" if patch.was_ensemble else ""
        fix_section = f"""
> **🤖 AI Arbiter Analysis** ({patch.provider}){ensemble_tag}
> **Cause:** {patch.root_cause}
> **Fix:** `{patch.fix_recommendation}`
"""
    elif finding.remediation_hint and not finding.suppressed:
        fix_section = f"\n**Suggested Fix:** {finding.remediation_hint}\n"

    evidence_lines = "\n".join(f"- `{e.file}:{e.line}` — {e.description}" for e in finding.evidence)
    
    # Use generic names for actors in the public PR comment by default to limit exposure?
    # No, PR comments are internal to the repo. We can show actual IDs.
    actor_1 = f"`{finding.actor_1_id}`"
    actor_2 = f"`{finding.actor_2_id}`"
    actors = f"{actor_1} & {actor_2}" if finding.actor_1_id != finding.actor_2_id else f"{actor_1} (multi-replica)"
    
    return f"""
<details open>
<summary><b>{sev_emoji} [{idx}] {finding.collision_type}</b> (Confidence: {int(finding.confidence * 100)}%)</summary>

- **Actors:** {actors}
- **Shared Target:** `{finding.shared_target_id}`
- **Atomic Protection:** {"✅ Yes" if finding.atomic_protection else "❌ No"}

**Evidence:**
{evidence_lines}
{fix_section}
</details>
"""


def generate_markdown_report(report: AnalysisReport) -> str:
    """Convert an AnalysisReport into a GitHub-flavored Markdown string."""
    header = f"{_COMMENT_MARKER}\n## ⬡ OmniGraph Architecture Scan\n\n"
    
    if report.critical_count == 0 and report.warning_count == 0:
        header += "✅ **PASS** — No distributed race conditions or architectural hazards detected.\n"
        return header

    status = "❌ **FAIL** — Critical race conditions detected. Block merge." if report.critical_count > 0 else "⚠️ **WARNING** — Hazards detected. Review recommended."
    
    summary = f"""
{status}

| Metric | Value |
|--------|-------|
| 🚨 Critical | **{report.critical_count}** |
| ⚠️ Warnings | **{report.warning_count}** |
| 🔍 Services Scanned | {report.total_services} |
| 🕸️ Edges Analyzed | {report.total_edges} |

### Findings
"""
    actionable = [f for f in report.findings if f.is_actionable]
    findings_md = "\n".join(_format_finding(i + 1, f) for i, f in enumerate(actionable))
    
    suppressed_count = len([f for f in report.findings if not f.is_actionable])
    footer = f"\n\n*_{suppressed_count} findings were automatically suppressed as false positives by architectural context._*" if suppressed_count > 0 else ""
    
    return header + summary + findings_md + footer


def post_to_github_pr(report: AnalysisReport, repo: str, pr_number: int, token: str) -> bool:
    """
    Post or update the OmniGraph report on a GitHub Pull Request.
    
    Args:
        report: The AnalysisReport.
        repo: Repository full name (e.g., 'owner/repo').
        pr_number: The PR number.
        token: GitHub API token (GITHUB_TOKEN).
        
    Returns:
        True if successful, False otherwise.
    """
    body = generate_markdown_report(report)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
    }
    
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    comments_url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
    
    try:
        # 1. Find existing comment
        resp = requests.get(comments_url, headers=headers)
        resp.raise_for_status()
        comments = resp.json()
        
        existing_comment_id = None
        for c in comments:
            if _COMMENT_MARKER in c.get("body", ""):
                existing_comment_id = c["id"]
                break
                
        # 2. Update or Create
        if existing_comment_id:
            update_url = f"{api_base}/repos/{repo}/issues/comments/{existing_comment_id}"
            res = requests.patch(update_url, headers=headers, json={"body": body})
            res.raise_for_status()
            return True
        else:
            res = requests.post(comments_url, headers=headers, json={"body": body})
            res.raise_for_status()
            return True
            
    except Exception as e:
        print(f"Failed to post GitHub PR comment: {e}")
        return False
