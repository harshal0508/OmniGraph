"""
scripts/check_listener.py
─────────────────────────────────────────────────────────────────────────────
PR-check polling daemon.

Polls all repos defined in OMNIGRAPH_REPOS (loaded from .env via config.py).
When a new PR is opened, it parses the changed files, queries the Neo4j graph
for cross-repo table collisions, and posts a warning comment if unsafe.

Configuration (all via .env):
  GITHUB_TOKEN       - GitHub personal access token with repo read + issue comment permissions
  OMNIGRAPH_REPOS    - JSON map of {"owner/repo": "service_id", ...}
  NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD - Neo4j connection (defaults match docker-compose)
"""

import sys
import time
from pathlib import Path
from github import Github

from core.graph.neo4j_builder import Neo4jGraphBuilder
from core.ingestion.ast_parser import ASTParser, EdgeType
from core.graph.target_resolver import resolve_target
from config import GITHUB_TOKEN, DEFAULT_REPOS


def run_check_listener():
    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN is not set. Add it to your .env file.")
        sys.exit(1)

    if not DEFAULT_REPOS:
        print("[ERROR] OMNIGRAPH_REPOS is empty. Add at least one repo mapping to your .env file.")
        print('  Example: OMNIGRAPH_REPOS={"owner/my-repo": "svc_myservice"}')
        sys.exit(1)

    g = Github(GITHUB_TOKEN)
    builder = Neo4jGraphBuilder()
    parser = ASTParser()

    # Bootstrap: track existing open PRs across ALL configured repos so we
    # only react to PRs opened after the listener starts.
    seen_prs: dict[str, set[int]] = {}
    repo_objects = {}
    for repo_name, service_id in DEFAULT_REPOS.items():
        try:
            repo = g.get_repo(repo_name)
            repo_objects[repo_name] = (repo, service_id)
            seen_prs[repo_name] = {pr.number for pr in repo.get_pulls(state='open')}
            print(f"[{repo_name}] tracking {len(seen_prs[repo_name])} existing open PRs as {service_id}")
        except Exception as e:
            print(f"[WARN] Could not access {repo_name}: {e}")

    print("\nStarting polling loop for NEW pull requests (Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(10)
            for repo_name, (repo, service_id) in repo_objects.items():
                try:
                    current_prs = repo.get_pulls(state='open')
                    for pr in current_prs:
                        if pr.number not in seen_prs[repo_name]:
                            seen_prs[repo_name].add(pr.number)
                            print(f"\n>>> [{repo_name}] New PR #{pr.number} detected — running OmniGraph check...")
                            _check_pr(pr, repo, service_id, repo_name, parser, builder)
                except Exception as e:
                    print(f"[WARN] Error polling {repo_name}: {e}")
    except KeyboardInterrupt:
        print("\nShutting down check listener.")
    finally:
        builder.close()


def _check_pr(pr, repo, service_id, repo_name, parser, builder):
    pr_writes = []
    func_locks: dict[str, str] = {}

    for pr_file in pr.get_files():
        if not (pr_file.filename.endswith(".py") or pr_file.filename.endswith(".js") or pr_file.filename.endswith(".ts")):
            continue
        try:
            content = repo.get_contents(pr_file.filename, ref=pr.head.ref).decoded_content.decode("utf-8")
        except Exception as e:
            print(f"  [WARN] Could not fetch {pr_file.filename}: {e}")
            continue

        tmp_path = Path(f"tmp_omnigraph_{pr_file.filename.replace('/', '_')}")
        tmp_path.write_text(content, encoding="utf-8")

        try:
            parsed = parser.parse_file(tmp_path, service_id, repo_name)
        finally:
            tmp_path.unlink(missing_ok=True)

        for edge in parsed.edges:
            if edge.edge_type in (EdgeType.USES_LOCK, EdgeType.USES_TRANSACTION):
                func_locks[edge.source_id] = (edge.metadata or {}).get("pattern", "unknown_lock")

        for edge in parsed.edges:
            target_id = resolve_target(edge.target_id, edge.metadata or {})
            if edge.edge_type in (EdgeType.WRITES_TO, EdgeType.READS_FROM) and not target_id.startswith("__"):
                pr_writes.append({
                    "service_id": parsed.service_id,
                    "service_name": parsed.service_name,
                    "func_id": edge.source_id,
                    "func_name": edge.source_id.split(":")[-1] if ":" in edge.source_id else edge.source_id,
                    "table_id": target_id,
                    "has_lock": edge.source_id in func_locks,
                    "lock_mechanism": func_locks.get(edge.source_id),
                    "access_type": edge.edge_type.name,
                })

    print(f"  Parsed {len(pr_writes)} IO operations from PR diff.")
    if not pr_writes:
        print("  No database interactions found. PR is safe.")
        return

    query = """
    UNWIND $pr_writes AS pr
    MATCH (t:Table {id: pr.table_id})<-[e2:WRITES_TO|READS_FROM]-(f2:Function)<-[:HAS_FUNCTION]-(s2:Service)
    WHERE s2.id <> pr.service_id
    OPTIONAL MATCH (f2)-[lock_edge:USES_LOCK|USES_TRANSACTION]->(t)
    RETURN pr.func_name AS f1_name, pr.has_lock AS f1_has_lock, pr.lock_mechanism AS f1_mechanism,
           s2.name AS service2, f2.name AS f2_name, lock_edge IS NOT NULL AS f2_has_lock, lock_edge.pattern AS f2_mechanism,
           t.id AS table_name
    """
    with builder.driver.session() as session:
        records = list(session.run(query, pr_writes=pr_writes))

    print(f"  Found {len(records)} structural overlaps with other services.")
    unsafe = []
    for r in records:
        if not r["f1_has_lock"] and not r["f2_has_lock"]:
            unsafe.append((r, "No locks on either side"))
        elif r["f1_has_lock"] != r["f2_has_lock"]:
            unsafe.append((r, "One-sided lock"))
        elif r["f1_mechanism"] != r["f2_mechanism"]:
            unsafe.append((r, f"Mismatched mechanisms: {r['f1_mechanism']} vs {r['f2_mechanism']}"))

    if not unsafe:
        print("  No unsafe collisions. PR is safe.")
        return

    comment = "### 🛑 OmniGraph Concurrency Warning\n\n"
    comment += f"This PR touches a shared table. **{len(unsafe)} unsafe cross-repo collision(s) detected.**\n\n"
    comment += "| Table | Colliding Service | Function | Reason |\n"
    comment += "|---|---|---|---|\n"
    for r, reason in unsafe:
        comment += f"| `{r['table_name']}` | `{r['service2']}` | `{r['f2_name']}` | {reason} |\n"
    comment += "\n*Please add matching distributed coordination before merging.*"

    try:
        pr.create_issue_comment(comment)
        print(f"  ⚠️  Warning posted to PR #{pr.number}.")
    except Exception as e:
        print(f"  [WARN] Could not post comment: {e}")


if __name__ == "__main__":
    run_check_listener()
