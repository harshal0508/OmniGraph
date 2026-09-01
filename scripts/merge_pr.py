import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.ingestion.iac_parser import IaCParser
from core.ingestion.ast_parser import ASTParser
from core.graph.target_resolver import resolve_target
from core.graph.neo4j_builder import Neo4jGraphBuilder

def merge_pr(repo_path: str, service_id: str, dry_run: bool = True):
    repo_dir = Path(repo_path)
    if not repo_dir.exists() or not repo_dir.is_dir():
        print(f"Error: {repo_path} is not a valid directory.")
        sys.exit(1)
    iac_parser_obj = IaCParser()
    iac_result = iac_parser_obj.parse_directory(repo_dir)
    # Check for overrides
    override_path = repo_dir / ".omnigraph.yml"
    if override_path.exists():
        iac_parser_obj.load_overrides(override_path)
    db_id = iac_result.service_to_db.get(service_id)
    if not db_id:
        svc_overrides = iac_parser_obj.overrides.get("overrides_by_service", {}).get(service_id, {})
        if "identity" in svc_overrides:
            db_id = svc_overrides["identity"]
        else:
            # Check global identities if they just mapped db_unknown_{service_id}
            raw_fallback = f"db_unknown_{service_id}"
            global_idents = iac_parser_obj.overrides.get("database_identities", {})
            if raw_fallback in global_idents:
                val = global_idents[raw_fallback]
                db_id = val.get("identity") if isinstance(val, dict) else val
            else:
                db_id = raw_fallback


    parser = ASTParser()

    print(f"Parsing repository {repo_path}...")
    
    total_edges = 0
    resolved_edges = 0
    dropped_edges = []
    
    # Track final parsed results to merge
    valid_services = []

    for root, _, files in os.walk(repo_dir):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in ('.py', '.js', '.ts'):
                file_path = Path(root) / file
                try:
                    result = parser.parse_file(file_path, service_id)
                    
                    # Filter and resolve edges
                    valid_edges = []
                    for edge in result.edges:
                        total_edges += 1
                        if edge.target_id.startswith('__'):
                            resolved = resolve_target(edge.target_id, edge.metadata)

                            if resolved and not resolved.startswith('__'):
                                edge.target_id = f"table_{db_id}_{resolved}"
                                edge.metadata["database_id"] = db_id
                                valid_edges.append(edge)
                                resolved_edges += 1
                            else:
                                dropped_edges.append({
                                    "timestamp": datetime.utcnow().isoformat(),
                                    "repo": repo_path,
                                    "file": str(file_path),
                                    "method": edge.metadata.get("method", "unknown"),
                                    "original_target": edge.target_id
                                })
                        else:
                            valid_edges.append(edge)
                            resolved_edges += 1
                    
                    result.edges = valid_edges
                    valid_services.append(result)
                except Exception as e:
                    print(f"Failed to parse {file_path}: {e}")

    hit_rate = (resolved_edges / total_edges) * 100 if total_edges > 0 else 100.0
    print("\n=== Resolution Stats ===")
    print(f"Total Edges Extracted: {total_edges}")
    print(f"Successfully Resolved: {resolved_edges}")
    print(f"Dropped Edges:         {len(dropped_edges)}")
    print(f"Resolution Hit Rate:   {hit_rate:.2f}%")
    
    # Write to dropped edges log
    if dropped_edges:
        log_path = Path('logs/dropped_edges.jsonl')
        with open(log_path, 'a') as f:
            for drop in dropped_edges:
                f.write(json.dumps(drop) + '\n')
        print(f"\n[!] Logged {len(dropped_edges)} dropped edges to logs/dropped_edges.jsonl")

    if dry_run:
        print("\n[DRY RUN] Would merge the following fully resolved edges:")
        for svc in valid_services:
            for e in svc.edges:
                print(f"  {e.source_id} --[{e.edge_type.name}]--> {e.target_id}")
        
        if hit_rate < 60.0:
            print(f"\n[WARNING] Dry run hit rate ({hit_rate:.2f}%) is below the 60% acceptance threshold.")
        else:
            print(f"\n[SUCCESS] Dry run hit rate ({hit_rate:.2f}%) meets the acceptance criteria.")
        return

    # Real ingestion
    if hit_rate < 60.0:
        print(f"\n[ABORT] Hit rate ({hit_rate:.2f}%) is below 60% threshold. Aborting merge.")
        sys.exit(1)
        
    print("\nMerging into Neo4j...")
    db = Neo4jGraphBuilder()
    for svc in valid_services:
        db.add_parsed_service(svc)
    db.close()
    print("Merge complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge a PR directory into OmniGraph")
    parser.add_argument("repo_path", help="Path to the repository/PR directory")
    parser.add_argument("service_id", help="Service ID identifier (e.g. 'order_service')")
    parser.add_argument("--dry-run", action="store_true", help="Perform resolution without writing to Neo4j")
    
    args = parser.parse_args()
    merge_pr(args.repo_path, args.service_id, args.dry_run)
