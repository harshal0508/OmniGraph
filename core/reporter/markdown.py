from core.schema import AnalysisReport, Severity

class MarkdownReporter:
    """Generates human-readable Markdown reports from the analysis results."""
    
    @staticmethod
    def generate(report: AnalysisReport) -> str:
        lines = []
        lines.append(f"# OmniGraph Analysis Report")
        lines.append(f"**Scan ID:** `{report.scan_id}`")
        lines.append(f"**Source Path:** `{report.source_path}`")
        lines.append(f"**Services Scanned:** {report.total_services}")
        lines.append(f"**Edges Extracted:** {report.total_edges}")
        lines.append("")
        
        actionable = [f for f in report.findings if f.is_actionable]
        suppressed = [f for f in report.findings if f.suppressed]
        
        lines.append(f"## Summary")
        lines.append(f"- 🔴 **Critical:** {report.critical_count}")
        lines.append(f"- 🟡 **Warnings:** {report.warning_count}")
        lines.append(f"- ⚪ **Suppressed (False Positives):** {len(suppressed)}")
        lines.append("")
        
        if actionable:
            lines.append("## Actionable Findings")
            for idx, finding in enumerate(actionable, 1):
                icon = "🔴" if finding.severity == Severity.CRITICAL else "🟡"
                lines.append(f"### {icon} Finding {idx}: {finding.collision_type}")
                lines.append(f"**Target:** `{finding.shared_target_id}`")
                lines.append(f"**Actors:** `{finding.actor_1_id}` ⚡ `{finding.actor_2_id}`")
                lines.append(f"**Confidence:** {finding.confidence * 100:.0f}%")
                lines.append("")
                lines.append(f"**Arbiter Reasoning:**")
                lines.append(f"> {finding.suppression_reason}")
                lines.append("")
                if finding.remediation_hint:
                    lines.append(f"**Suggested Fix:**")
                    lines.append(f"{finding.remediation_hint}")
                lines.append("")
                
        if suppressed:
            lines.append("## Suppressed Candidates")
            lines.append("These structural collisions were identified but ruled out by the LLM Arbiter.")
            for finding in suppressed:
                lines.append(f"- `{finding.actor_1_id}` vs `{finding.actor_2_id}` on `{finding.shared_target_id}`")
                lines.append(f"  *Reason: {finding.suppression_reason}*")
                
        return "\n".join(lines)
