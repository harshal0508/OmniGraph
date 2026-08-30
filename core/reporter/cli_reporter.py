"""
core/reporter/cli_reporter.py
Rich terminal output for OmniGraph analysis results.
"""

from __future__ import annotations

import json
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from core.schema import AnalysisReport, CollisionFinding, Severity
from core.arbiter.arbiter import get_patch

# Safe on all Windows terminals - no unicode box chars
console = Console(highlight=False, force_terminal=True, safe_box=True)

_SEVERITY_STYLE: dict[str, tuple[str, str]] = {
    Severity.CRITICAL.value: ("bold red",    "[CRITICAL]"),
    Severity.WARNING.value:  ("bold yellow", "[WARNING]"),
    Severity.INFO.value:     ("dim cyan",    "[INFO]"),
}


def _severity_text(severity: str) -> Text:
    style, label = _SEVERITY_STYLE.get(severity, ("white", severity))
    return Text(label, style=style)


def _print_header(report: AnalysisReport) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = Text()
    header.append("  [OG] OmniGraph  ", style="bold white on #0d1117")
    header.append("Context-Aware Architectural & Concurrency Analysis\n", style="dim white")
    header.append(f"  Scan ID  : {report.scan_id}\n", style="dim")
    header.append(f"  Target   : {report.source_path}\n", style="dim")
    header.append(f"  Timestamp: {ts}", style="dim")
    console.print(Panel(header, border_style="bright_blue", padding=(0, 1)))


def _print_graph_summary(report: AnalysisReport) -> None:
    console.print("[bold blue]--- Graph Topology ---[/bold blue]")
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold blue")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right", style="bold white")
    table.add_row("Services indexed",  str(report.total_services))
    table.add_row("Total graph edges", str(report.total_edges))
    table.add_row("Findings detected", str(len(report.findings)))
    table.add_row("  >> Critical",     f"[bold red]{report.critical_count}[/bold red]")
    table.add_row("  >> Warnings",     f"[bold yellow]{report.warning_count}[/bold yellow]")
    console.print(table)


def _print_finding(idx: int, finding: CollisionFinding) -> None:
    sev_text  = _severity_text(finding.severity.value)
    conf_pct  = f"{int(finding.confidence * 100)}%"
    suppressed = " [dim](suppressed)[/dim]" if finding.suppressed else ""

    title_text = Text()
    title_text.append(f"  [{idx}] ", style="dim")
    title_text.append(finding.collision_type, style="bold white")
    title_text.append(f"  confidence {conf_pct}", style="dim")
    title_text.append(suppressed)

    border = "dim" if finding.suppressed else (
        "red" if finding.severity == Severity.CRITICAL else "yellow"
    )

    detail_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    detail_table.add_column("Key",   style="dim", width=18)
    detail_table.add_column("Value", style="white")

    detail_table.add_row("Severity",      sev_text)
    detail_table.add_row("Actor 1",       f"[bold cyan]{finding.actor_1_id}[/bold cyan]")
    detail_table.add_row("Actor 2",       f"[bold cyan]{finding.actor_2_id}[/bold cyan]")
    detail_table.add_row("Shared Target", f"[bold magenta]{finding.shared_target_id}[/bold magenta]")
    detail_table.add_row("Atomic Lock",   "[green]YES[/green]" if finding.atomic_protection else "[red]NO[/red]")
    detail_table.add_row("Confidence",    f"[bold]{conf_pct}[/bold]")

    if finding.suppression_reason:
        detail_table.add_row("Suppressed", f"[dim]{finding.suppression_reason}[/dim]")

    if finding.evidence:
        ev_lines = "\n".join(f"  * {e}" for e in finding.evidence)
        detail_table.add_row("Evidence", f"[dim]{ev_lines}[/dim]")

    # Check for LLM Arbiter patch
    patch = get_patch(finding)
    if patch and not finding.suppressed:
        ensemble_marker = " (Ensemble)" if patch.was_ensemble else ""
        patch_desc = (
            f"[bold cyan]AI Arbiter Analysis[/bold cyan] ({patch.provider}{ensemble_marker}):\n"
            f"[dim]Cause:[/dim] {patch.root_cause}\n"
            f"[dim]Fix:[/dim]   [bold green]{patch.fix_recommendation}[/bold green]"
        )
        detail_table.add_row("AI Patch", patch_desc)
    elif finding.remediation_hint and not finding.suppressed:
        detail_table.add_row("Hint", f"[bold green]{finding.remediation_hint}[/bold green]")

    console.print(Panel(detail_table, title=title_text, border_style=border, padding=(0, 1)))


def _print_findings(report: AnalysisReport) -> None:
    console.print('-' * 79)
    actionable = [f for f in report.findings if f.is_actionable]
    suppressed = [f for f in report.findings if not f.is_actionable]

    if not report.findings:
        console.print(Panel(
            "[bold green]PASS - No race conditions detected.[/bold green]\n"
            "[dim]The graph found no unprotected concurrent writes to shared datastores.[/dim]",
            border_style="green",
        ))
        return

    for idx, finding in enumerate(actionable, start=1):
        _print_finding(idx, finding)

    if suppressed:
        console.print(
            f"\n[dim]INFO: {len(suppressed)} finding(s) suppressed by detection rules "
            f"(DB isolation, queue-mediated, etc.). Use --show-suppressed to view.[/dim]"
        )


def _print_warnings(report: AnalysisReport) -> None:
    if not report.warnings:
        return
    console.print('-' * 79)
    for w in report.warnings:
        console.print(f"  [yellow]{w}[/yellow]")


def _print_verdict(report: AnalysisReport) -> None:
    console.print('-' * 79)
    if report.critical_count == 0:
        verdict_text = Text()
        verdict_text.append("  PASS  ", style="bold green")
        verdict_text.append(
            f"No critical race conditions detected. "
            f"{report.warning_count} warning(s) for manual review.",
            style="dim"
        )
        console.print(Panel(verdict_text, border_style="green"))
    else:
        verdict_text = Text()
        verdict_text.append("  FAIL  ", style="bold red")
        verdict_text.append(
            f"{report.critical_count} critical race condition(s) detected. "
            f"Block merge until resolved.",
            style="bold white"
        )
        console.print(Panel(verdict_text, border_style="red"))


def export_json(report: AnalysisReport) -> str:
    findings_data = []
    for f in report.findings:
        data = {
            **f.to_scrubbed_dict(),
            "suppressed":         f.suppressed,
            "suppression_reason": f.suppression_reason,
            "remediation_hint":   f.remediation_hint,
            "evidence":           [str(e) for e in f.evidence],
            "is_actionable":      f.is_actionable,
        }
        patch = get_patch(f)
        if patch:
            data["llm_patch"] = {
                "provider": patch.provider,
                "was_ensemble": patch.was_ensemble,
                "is_genuine_race": patch.is_genuine_race,
                "root_cause": patch.root_cause,
                "fix_recommendation": patch.fix_recommendation,
                "fix_pattern": patch.fix_pattern,
                "arbiter_confidence": patch.arbiter_confidence,
            }
        findings_data.append(data)
    return json.dumps({
        "scan_id":        report.scan_id,
        "source_path":    report.source_path,
        "total_services": report.total_services,
        "total_edges":    report.total_edges,
        "critical_count": report.critical_count,
        "warning_count":  report.warning_count,
        "findings":       findings_data,
        "warnings":       report.warnings,
    }, indent=2)


def print_report(report: AnalysisReport, json_output: bool = False) -> None:
    if json_output:
        console.print_json(export_json(report))
        return
    _print_header(report)
    _print_graph_summary(report)
    _print_findings(report)
    _print_warnings(report)
    _print_verdict(report)
