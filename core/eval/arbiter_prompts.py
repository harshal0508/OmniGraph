import json
from pathlib import Path
from core.eval.benchmark import Neo4jGraphBuilder, IaCParser, ASTParser
from core.graph.boundary_resolver import normalize_table_name

from core.eval.arbiter_prompt_template import PROMPT_TEMPLATE

def generate_prompts():
    manifest_path = Path("tests/eval_dataset/manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
        
    eval_dir = Path("tests/eval_dataset")
    builder = Neo4jGraphBuilder()
    
    prompts = {}
    
    for case in manifest["cases"]:
        case_id = case["id"]
        # Parse into Neo4j
        case_dir = eval_dir / "fixtures" / case_id.replace("_negative", "")
        if not case_dir.exists():
             case_dir = eval_dir / "fixtures" / case_id
             
        builder.clear_database()
        iac = IaCParser().parse_directory(case_dir)
        builder.add_iac_parsed(iac)
        
        for file_path in case_dir.rglob("*.*"):
            if file_path.suffix in [".py", ".js", ".ts"]:
                content = file_path.read_text(encoding="utf-8")
                hint = "unknown"
                if "Order" in content: hint = "orders"
                elif "UserProfile" in content: hint = "user_profile"
                elif "User" in content: hint = "users"
                elif "Wallet" in content: hint = "wallet"
                elif "Cart" in content: hint = "cart"
                elif "EventPromo" in content: hint = "event_promo_code"
                elif "Room" in content: hint = "room"
                elif "AuditLog" in content: hint = "audit_log"
                
                svc_id = f"svc_{file_path.stem}"
                parsed = ASTParser().parse_file(file_path, service_id=svc_id)
                db_id = iac.service_to_db.get(svc_id, f"db_unknown_{svc_id}")
                for edge in parsed.edges:
                    if edge.target_id in ("__UNRESOLVED_TABLE__", "__SQL_WRITE_TARGET__", "__SQL_READ_TARGET__"):
                        base_table = normalize_table_name(hint)
                        edge.target_id = f"table_{db_id}_{base_table}"
                        
                builder.add_parsed_service(parsed)
                
        with builder.driver.session() as session:
            # Query Tier 2 candidates
            tier_2_query = """
            MATCH (s:Service)-[:HAS_FUNCTION]->(f1:Function)-[e1:WRITES_TO]->(t:Table)<-[e2:WRITES_TO|READS_FROM]-(f2:Function)<-[:HAS_FUNCTION]-(s)
            WHERE f1 <> f2 AND NOT (f1)-[:USES_LOCK|USES_TRANSACTION]->(t) AND NOT (f2)-[:USES_LOCK|USES_TRANSACTION]->(t)
            OPTIONAL MATCH (f1)-[c:CALLS]-(f2)
            RETURN s.name as service, s.replica_count as replicas, 
                   f1.name as f1_name, type(e1) as f1_access, 
                   f2.name as f2_name, type(e2) as f2_access,
                   t.id as table_name,
                   CASE WHEN c IS NOT NULL THEN 'direct_call' ELSE 'none' END as flow_evidence
            LIMIT 1
            """
            result = list(session.run(tier_2_query))
            if result:
                r = result[0]
                prompt = PROMPT_TEMPLATE.format(
                    category="cross_function_same_service",
                    f1_name=r["f1_name"],
                    f1_access=r["f1_access"],
                    f2_name=r["f2_name"],
                    f2_access=r["f2_access"],
                    table=r["table_name"],
                    service=r["service"],
                    replicas=r["replicas"],
                    lock="none",
                    transaction="none",
                    flow_evidence=r["flow_evidence"]
                )
                prompts[case_id] = prompt
                
    with open("arbiter_prompts.json", "w") as f:
        json.dump(prompts, f, indent=2)

if __name__ == "__main__":
    generate_prompts()
