import time
import random
import os
import sys

# Ensure core can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.graph.neo4j_builder import Neo4jGraphBuilder
from core.schema import (
    ServiceNode, DatabaseNode, TableNode, QueueNode, FunctionNode, GraphEdge, EdgeType
)
from core.ingestion.ast_parser import ParsedService
from core.ingestion.iac_parser import IaCParseResult

def generate_synthetic_graph(num_services=100, num_tables=400, funcs_per_service=20, edges_per_func=2):
    print(f"Generating synthetic graph data...")
    print(f"  Services: {num_services}")
    print(f"  Tables:   {num_tables}")
    print(f"  Functions: {num_services * funcs_per_service}")
    
    iac = IaCParseResult()
    
    db_nodes = []
    for i in range(num_tables // 10):
        db = DatabaseNode(id=f"db_{i}", name=f"database_{i}", db_type="postgres")
        iac.databases.append(db)
        db_nodes.append(db)
        
    table_ids = []
    for i in range(num_tables):
        t_id = f"table_{i}"
        table_ids.append(t_id)
        
    services = []
    for i in range(num_services):
        svc = ServiceNode(id=f"svc_{i}", name=f"service_{i}", language="python")
        iac.services.append(svc)
        services.append(svc)
        
    parsed_services = []
    for svc in services:
        ps = ParsedService(
            service_id=svc.id,
            service_name=svc.name,
            language=svc.language,
            source_file=f"src/{svc.name}/main.py",
            repo_name="synthetic_repo",
            branch="main",
            commit_sha="a1b2c3d4"
        )
        
        for j in range(funcs_per_service):
            func = FunctionNode(
                id=f"func_{svc.id}_{j}",
                name=f"handler_{j}",
                service_id=svc.id,
                source_file=ps.source_file,
                start_line=10 * j,
                end_line=10 * j + 5,
                repo_name="synthetic_repo",
                branch="main",
                commit_sha="a1b2c3d4"
            )
            ps.functions.append(func)
            
            for k in range(edges_per_func):
                target_table = random.choice(table_ids)
                edge_type = random.choice([EdgeType.READS_FROM, EdgeType.WRITES_TO])
                
                # HOT TABLE (Highly realistic fan-out)
                if random.random() < 0.05:
                    target_table = "table_hot_orders"
                    edge_type = EdgeType.WRITES_TO
                    
                edge = GraphEdge(
                    source_id=func.id,
                    target_id=target_table,
                    edge_type=edge_type,
                    source_file=ps.source_file,
                    source_line=func.start_line + 2,
                    repo_name="synthetic_repo",
                    branch="main",
                    commit_sha="a1b2c3d4"
                )
                ps.edges.append(edge)
                
        parsed_services.append(ps)
        
    return iac, parsed_services


def run_load_test():
    builder = Neo4jGraphBuilder()
    
    print("Clearing database...")
    builder.clear_database()
    
    print("Initializing schema (creating constraints)...")
    builder.initialize_schema()
    
    iac, parsed_services = generate_synthetic_graph()
    
    print("\\n--- INGESTION ---")
    start_time = time.time()
    builder.add_iac_parsed(iac)
    
    total_svcs = len(parsed_services)
    for i, ps in enumerate(parsed_services):
        if i > 0 and i % 50 == 0:
            print(f"  Ingested {i}/{total_svcs} services... ({(time.time() - start_time):.2f}s)")
        builder.add_parsed_service(ps)
        
    ingest_time = time.time() - start_time
    print(f"Total ingestion time: {ingest_time:.2f}s")
    
    print("\\n--- QUERY LATENCY ---")
    
    tier_2_query = """
    MATCH (s1:Service)-[:HAS_FUNCTION]->(f1:Function)-[e1:WRITES_TO]->(t:Table)<-[e2:WRITES_TO|READS_FROM]-(f2:Function)<-[:HAS_FUNCTION]-(s2:Service)
    WHERE s1 <> s2 
      AND NOT (f1)-[:USES_LOCK|USES_TRANSACTION]->(t) 
      AND NOT (f2)-[:USES_LOCK|USES_TRANSACTION]->(t)
    OPTIONAL MATCH (f1)-[c:CALLS]-(f2)
    RETURN s1.name as service1, s2.name as service2, 
           f1.name as f1_name, type(e1) as f1_access, 
           f2.name as f2_name, type(e2) as f2_access,
           t.id as table_name,
           CASE WHEN c IS NOT NULL THEN 'direct_call' ELSE 'none' END as flow_evidence
    LIMIT 100
    """
    
    with builder.driver.session() as session:
        session.run("MATCH (n) RETURN count(n)").single()
        
        q_start = time.time()
        result = session.run(tier_2_query)
        records = list(result)
        q_end = time.time()
        
        print(f"Found {len(records)} cross-service race condition candidates.")
        print(f"Query latency: {(q_end - q_start):.4f}s")
        
    print("\\n--- PROFILE ---")
    profile_query = "PROFILE " + tier_2_query
    with builder.driver.session() as session:
        res = session.run(profile_query)
        profile = res.consume().profile
        
        def print_profile_node(node, depth=0):
            indent = "  " * depth
            print(f"{indent}- {node['operatorType']} (rows={node['rows']})")
            for child in node.get('children', []):
                print_profile_node(child, depth + 1)
                
        print_profile_node(profile)

if __name__ == "__main__":
    run_load_test()
