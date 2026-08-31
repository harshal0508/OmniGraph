from __future__ import annotations

# ── Windows UTF-8 fix ────────────────────────────────────────────────────────
import os
if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# ─────────────────────────────────────────────────────────────────────────────


"""
main.py
─────────────────────────────────────────────────────────────────────────────
OmniGraph CLI Entry Point.

Usage:
  python main.py scan <path>                    # Scan a directory
  python main.py scan <path> --json             # Output JSON (for CI/CD)
  python main.py scan <path> --show-suppressed  # Show all findings
  python main.py demo                           # Run on built-in fixtures
"""

import sys
import uuid
from pathlib import Path


import click
from rich.console import Console

from core.ingestion.ast_parser import ASTParser, parse_service_directory
from core.ingestion.iac_parser import IaCParser
from core.graph.builder import GraphBuilder
from core.graph.rules.race_condition import detect_race_conditions
from core.graph.rules.suppressors import apply_all_suppressors
from core.graph.rules.toctou import detect_toctou
from core.graph.rules.redis_state import detect_redis_state_races
from core.graph.rules.retry_hazard import detect_retry_hazards
from core.graph.rules.saga_detector import suppress_saga_findings
from core.arbiter.arbiter import OmniGraphArbiter
from core.reporter.cli_reporter import print_report, export_json
from core.schema import AnalysisReport

console = Console()

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


# ─── Helpers ──────────────────────────────────────────────────────────────────

from typing import Tuple
import networkx as nx

def _run_analysis(
    target_path: Path,
    show_suppressed: bool = False,
    no_llm: bool = False,
    json_output: bool = False,
) -> Tuple[AnalysisReport, nx.MultiDiGraph]:
    """
    Core analysis pipeline:
      1. Parse IaC files   → service/db/queue nodes + replica counts
      2. Parse source code → edges (WRITES_TO, READS_FROM, USES_LOCK...)
      3. Build graph       → unified NetworkX topology
      4. Run rules         → collision findings
      5. Apply suppressors → filter false positives
      6. Return report
    """
    scan_id = str(uuid.uuid4())[:8].upper()
    warnings: list[str] = []

    # ── Step 1: Parse IaC ──────────────────────────────────────────────────
    iac_parser = IaCParser()
    iac_result = iac_parser.parse_directory(target_path)
    warnings.extend(iac_result.warnings)

    # ── Step 2: Parse source code ──────────────────────────────────────────
    ast_parser = ASTParser()
    parsed_services = []

    # Discover service subdirectories OR treat root as single service
    service_dirs = [d for d in target_path.iterdir() if d.is_dir()
                    and d.name not in ("tests", "fixtures", ".git", "node_modules", "__pycache__")]

    if service_dirs:
        for svc_dir in sorted(service_dirs):
            svc_id = f"svc_{svc_dir.name.lower().replace('-', '_')}"
            parsed = parse_service_directory(svc_dir, service_id=svc_id, service_name=svc_dir.name)
            parsed_services.append(parsed)
            warnings.extend(parsed.warnings)
    else:
        # Single-service repo — treat entire directory as one service
        svc_id = f"svc_{target_path.name.lower().replace('-', '_')}"
        parsed = parse_service_directory(target_path, service_id=svc_id, service_name=target_path.name)
        parsed_services.append(parsed)
        warnings.extend(parsed.warnings)

    # ── Step 3: Build graph ────────────────────────────────────────────────
    builder = GraphBuilder()
    builder.add_iac_result(iac_result)
    for parsed in parsed_services:
        builder.add_parsed_service(parsed)

    graph = builder.build()
    summary = builder.summary()

    # ── Step 4: Run all detection rules ────────────────────────────────────
    findings = []

    # Phase 1: Cross-service distributed race conditions
    findings += detect_race_conditions(graph)

    # Phase 2a: TOCTOU (read-check-write without lock, same service)
    findings += detect_toctou(graph)

    # Phase 2b: Redis non-atomic read-modify-write
    findings += detect_redis_state_races(graph)

    # Phase 2c: Retry-amplification on non-idempotent writes
    findings += detect_retry_hazards(graph)

    # ── Step 5: Apply suppressors ──────────────────────────────────────────
    # Phase 1 suppressors: DB isolation, single-replica, config ambiguity
    findings = apply_all_suppressors(findings, graph, warnings)

    # Phase 2 suppressor: Saga coordinator pattern
    findings = suppress_saga_findings(findings, graph, warnings)

    # ── Step 6: Phase 3 — LLM Arbiter enrichment (BYOK, optional) ──────────
    arbiter = OmniGraphArbiter()
    if arbiter.is_online and not no_llm:
        if not json_output:
            console.print(
                f"[dim]LLM Arbiter active: {', '.join(arbiter.providers)} | "
                f"Enriching top findings...[/dim]"
            )
        findings = arbiter.enrich(findings, graph, max_findings=5)
    elif not no_llm and not arbiter.is_online:
        warnings.append(
            "LLM Arbiter offline: set ANTHROPIC_API_KEY or GOOGLE_API_KEY "
            "to enable AI-generated patch suggestions."
        )

    # ── Step 7: Build report ───────────────────────────────────────────────
    report = AnalysisReport(
        scan_id=scan_id,
        source_path=str(target_path),
        total_services=summary.get("total_nodes", 0),
        total_edges=summary.get("total_edges", 0),
        findings=findings,
        warnings=warnings,
    )

    return report, graph




# ─── CLI Commands ─────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="omnigraph")
def cli() -> None:
    """
    \b
    ⬡  OmniGraph — Architectural & Concurrency Static Analysis
    Detects distributed race conditions before they reach production.
    """



@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "json_output", is_flag=True, help="Output as JSON (for CI/CD pipelines).")
@click.option("--show-suppressed", is_flag=True, help="Include suppressed/informational findings.")
@click.option("--no-llm", is_flag=True, help="Run in strict offline mode (disable AI arbiter).")
@click.option("--ui", is_flag=True, help="Generate and open an interactive HTML dashboard.")
def scan(path: Path, json_output: bool, show_suppressed: bool, no_llm: bool, ui: bool) -> None:
    """Scan a repository directory for architectural race conditions."""
    if not json_output:
        console.print(f"\n[dim]Scanning: [bold]{path.resolve()}[/bold][/dim]\n")

    try:
        report, graph = _run_analysis(path, show_suppressed=show_suppressed, no_llm=no_llm, json_output=json_output)
    except Exception as exc:
        console.print(f"[bold red]Error during analysis:[/bold red] {exc}")
        sys.exit(2)

    print_report(report, json_output=json_output)

    if ui and not json_output:
        from core.reporter.ui_exporter import export_html_dashboard
        import webbrowser
        out_path = Path("omnigraph-report.html")
        export_html_dashboard(report, graph, out_path)
        console.print(f"\n[bold green]UI Dashboard generated at {out_path.resolve()}[/bold green]")
        webbrowser.open(out_path.absolute().as_uri())

    # Exit code 1 on critical findings (for CI/CD gate)
    if report.critical_count > 0:
        sys.exit(1)


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.option("--no-llm", is_flag=True, help="Run in strict offline mode (disable AI arbiter).")
@click.option("--ui", is_flag=True, help="Generate and open an interactive HTML dashboard.")
def demo(json_output: bool, no_llm: bool, ui: bool) -> None:
    """
    Run OmniGraph on the built-in vulnerable fixture scenarios.
    Great for verifying the installation works correctly.
    """
    demo_path = FIXTURES_DIR
    if not demo_path.exists():
        console.print("[red]Fixture directory not found. Run from the project root.[/red]")
        sys.exit(2)

    if not json_output:
        console.print(f"\n[dim]Running demo scenarios from: [bold]{demo_path.resolve()}[/bold][/dim]\n")

    try:
        report, graph = _run_analysis(demo_path, show_suppressed=False, no_llm=no_llm, json_output=json_output)
    except Exception as exc:
        console.print(f"[bold red]Error during demo analysis:[/bold red] {exc}")
        sys.exit(2)

    print_report(report, json_output=json_output)

    if ui and not json_output:
        from core.reporter.ui_exporter import export_html_dashboard
        import webbrowser
        out_path = Path("omnigraph-report.html")
        export_html_dashboard(report, graph, out_path)
        console.print(f"\n[bold green]UI Dashboard generated at {out_path.resolve()}[/bold green]")
        webbrowser.open(out_path.absolute().as_uri())

    if report.critical_count > 0:
        sys.exit(1)


@cli.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--repo", required=True, help="GitHub repository (owner/repo).")
@click.option("--pr", required=True, type=int, help="Pull request number.")
@click.option("--token", required=True, help="GitHub API token.")
@click.option("--no-llm", is_flag=True, help="Run in strict offline mode (disable AI arbiter).")
def github_pr(path: Path, repo: str, pr: int, token: str, no_llm: bool) -> None:
    """Run OmniGraph and post results as a GitHub PR comment."""
    console.print(f"\n[dim]Running GitHub Action scan on: [bold]{path.resolve()}[/bold][/dim]\n")
    try:
        report, _ = _run_analysis(path, show_suppressed=False, no_llm=no_llm, json_output=True)
    except Exception as exc:
        console.print(f"[bold red]Error during analysis:[/bold red] {exc}")
        sys.exit(2)

    from core.reporter.github_reporter import post_to_github_pr
    success = post_to_github_pr(report, repo, pr, token)
    
    if not success:
        console.print("[bold red]Failed to post PR comment.[/bold red]")
        sys.exit(2)
        
    console.print("[bold green]Successfully posted PR comment.[/bold green]")
    
    if report.critical_count > 0:
        sys.exit(1)




@cli.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("service_id", type=str)
def ingest(repo_path: Path, service_id: str) -> None:
    """Bulk scan and ingest a local repository into the Neo4j graph."""
    from scripts.merge_pr import merge_pr
    console.print(f"[dim]Running bulk ingestion on {repo_path} as {service_id}...[/dim]")
    merge_pr(str(repo_path), service_id, dry_run=False)

@cli.command()
def check_listener() -> None:
    """Run the PR-check polling daemon for active PR safety checks."""
    from scripts.check_listener import run_check_listener
    console.print("[dim]Starting PR-check polling loop...[/dim]")
    run_check_listener()

@cli.command()
def merge_listener() -> None:
    """Run the merge-trigger polling daemon for post-merge graph updates."""
    from scripts.merge_listener import run_merge_listener
    console.print("[dim]Starting merge-trigger polling loop...[/dim]")
    run_merge_listener()

@cli.command()
def benchmark() -> None:
    """Run the core evaluation benchmark suite."""
    import subprocess
    console.print("[dim]Running benchmark suite...[/dim]")
    result = subprocess.run([sys.executable, "core/eval/benchmark.py"])
    sys.exit(result.returncode)

if __name__ == "__main__":

    cli()
