import os, sys, time, json
from pathlib import Path
from dotenv import load_dotenv
from github import Github

sys.path.insert(0, os.path.abspath('.'))
from core.graph.neo4j_builder import Neo4jGraphBuilder
from core.ingestion.ast_parser import ASTParser, EdgeType

load_dotenv(Path(os.path.expanduser('~/.env')))
token = os.environ.get("GITHUB_TOKEN")
g = Github(token)
repo_py = g.get_repo("tzegoat9/python-mini-projects")

print("Initializing Neo4j and AST Parser...")
builder = Neo4jGraphBuilder()
parser = ASTParser()

print("Fetching existing PRs to establish baseline...")
seen_prs = {pr.number for pr in repo_py.get_pulls(state='open')}
print(f"Currently tracking {len(seen_prs)} open PRs.")

print("Starting polling loop for NEW pull requests...")
try:
    while True:
        time.sleep(5)
        current_prs = repo_py.get_pulls(state='open')
        for pr in current_prs:
            if pr.number not in seen_prs:
                seen_prs.add(pr.number)
                print(f"\\n>>> [EXTERNAL EVENT DETECTED] New PR #{pr.number} opened! Running OmniGraph PR Check pipeline...")
                
                pr_files = pr.get_files()
                pr_writes = []
                for pr_file in pr_files:
                    if pr_file.filename.endswith(".py"):
                        content = repo_py.get_contents(pr_file.filename, ref=pr.head.ref).decoded_content.decode('utf-8')
                        tmp_path = Path(f"tmp_{pr_file.filename.replace('/', '_')}")
                        tmp_path.write_text(content, encoding='utf-8')
                        
                        parsed = parser.parse_file(tmp_path, 'svc_python', 'tzegoat9/python-mini-projects')
                        tmp_path.unlink()
                        
                        # First pass: map which functions have locks
                        func_locks = {}
                        for edge in parsed.edges:
                            if edge.edge_type in (EdgeType.USES_LOCK, EdgeType.USES_TRANSACTION):
                                # Map function ID to its lock mechanism (e.g. 'pg_advisory_lock')
                                func_locks[edge.source_id] = edge.metadata.get("pattern", "unknown_lock") if edge.metadata else "unknown_lock"

                        for edge in parsed.edges:
                            # Use Unified Target Resolver to align PR AST with the graph
                            from core.graph.target_resolver import resolve_target
                            target_id = resolve_target(edge.target_id, edge.metadata or {})
                            
                            if edge.edge_type in (EdgeType.WRITES_TO, EdgeType.READS_FROM) and not target_id.startswith('__'):
                                pr_writes.append({
                                    "service_id": parsed.service_id,
                                    "service_name": parsed.service_name,
                                    "func_id": edge.source_id,
                                    "func_name": edge.source_id.split(':')[-1] if ':' in edge.source_id else edge.source_id,
                                    "table_id": target_id,
                                    "has_lock": edge.source_id in func_locks,
                                    "lock_mechanism": func_locks.get(edge.source_id),
                                    "access_type": edge.edge_type.name
                                })
                
                print(f"Dynamically parsed {len(pr_writes)} IO operations from PR code.")
                if len(pr_writes) > 0:
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
                        res = session.run(query, pr_writes=pr_writes)
                        records = list(res)
                    
                    print(f"Found {len(records)} raw structural overlaps.")
                    unsafe_records = []
                    for r in records:
                        f1_has = r['f1_has_lock']
                        f1_mech = r['f1_mechanism']
                        f2_has = r['f2_has_lock']
                        f2_mech = r['f2_mechanism']
                        
                        if not f1_has and not f2_has:
                            unsafe_records.append((r, "No locks on either side"))
                        elif f1_has != f2_has:
                            unsafe_records.append((r, "Partial Fix: one-sided lock"))
                        elif f1_mech != f2_mech:
                            unsafe_records.append((r, f"Mismatched mechanisms: {f1_mech} vs {f2_mech}"))
                            
                    print(f"Filtered to {len(unsafe_records)} unsafe collisions.")
                    if len(unsafe_records) > 0:
                        r, reason = unsafe_records[0]
                        comment = "### ⚠️ OmniGraph Concurrency Warning\n"
                        comment += f"This PR's write to `{r['table_name']}` is unsafe ({reason}).\n\n"
                        comment += "**Cross-Repo Collisions:**\n"
                        for r, reason in unsafe_records:
                            comment += f"- `{r['service2']}` interacts with this table (Function: `{r['f2_name']}`) -> {reason}.\n"
                        comment += "\n*Please add matching distributed coordination before merging.*"
                        
                        print("Posting PR comment...")
                        pr.create_issue_comment(comment)
                        print(f"Comment posted successfully to PR #{pr.number}! Workflow complete.")
                    else:
                        print("No unsafe collisions found. PR is safe.")
except KeyboardInterrupt:
    print("Shutting down.")
builder.close()
