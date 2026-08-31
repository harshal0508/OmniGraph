"""
core/graph/neo4j_builder.py
─────────────────────────────────────────────────────────────────────────────
Persistent Neo4j graph builder.
Replaces the in-memory NetworkX builder for cross-repo persistence (OmniGraph v2).
"""

from __future__ import annotations
from typing import Any
from neo4j import GraphDatabase

from core.schema import (
    ServiceNode, DatabaseNode, TableNode, QueueNode, MutexNode, GraphEdge
)
from core.ingestion.ast_parser import ParsedService
from core.ingestion.iac_parser import IaCParseResult

class Neo4jGraphBuilder:
    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = "omnigraph_secret_123"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        
    def initialize_schema(self):
        """Creates uniqueness constraints to ensure MERGE idempotency across repos."""
        with self.driver.session() as session:
            # Table uniqueness: Must be strictly unique by ID (which should be canonicalized)
            session.run("CREATE CONSTRAINT table_unique IF NOT EXISTS FOR (t:Table) REQUIRE t.id IS UNIQUE")
            session.run("CREATE CONSTRAINT service_unique IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE")
            session.run("CREATE CONSTRAINT database_unique IF NOT EXISTS FOR (d:Database) REQUIRE d.id IS UNIQUE")
            session.run("CREATE CONSTRAINT function_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.id IS UNIQUE")
            session.run("CREATE CONSTRAINT queue_unique IF NOT EXISTS FOR (q:Queue) REQUIRE q.id IS UNIQUE")

    def close(self):
        self.driver.close()

    def clear_database(self):
        """DANGER: Clears the entire graph. Useful for dev/tests."""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def _merge_service(self, tx, node: ServiceNode):
        query = """
        MERGE (s:Service {id: $id})
        SET s.name = $name,
            s.language = $language,
            s.replica_count = $replica_count,
            s.source_file = $source_file,
            s.repo_name = coalesce($repo_name, s.repo_name),
            s.branch = coalesce($branch, s.branch),
            s.commit_sha = coalesce($commit_sha, s.commit_sha)
        """
        tx.run(query, id=node.id, name=node.name, language=node.language,
               replica_count=node.replica_count, source_file=node.source_file or "",
               repo_name=node.repo_name, branch=node.branch, commit_sha=node.commit_sha)

    def _merge_database(self, tx, node: DatabaseNode):
        query = """
        MERGE (d:Database {id: $id})
        SET d.name = $name,
            d.db_type = $db_type,
            d.isolation_level = $isolation_level
        """
        tx.run(query, id=node.id, name=node.name, db_type=node.db_type,
               isolation_level=node.isolation_level.value)

    def _merge_table(self, tx, node: TableNode):
        query = """
        MERGE (t:Table {id: $id})
        ON CREATE SET t.name = $name,
                      t.database_id = $database_id
        ON MATCH SET t.name = coalesce(t.name, $name),
                     t.database_id = coalesce(t.database_id, $database_id)
        """
        tx.run(query, id=node.id, name=node.name, database_id=node.database_id)
        
        # Link to DB
        link_query = """
        MATCH (t:Table {id: $t_id})
        MATCH (d:Database {id: $d_id})
        MERGE (t)-[:BELONGS_TO]->(d)
        """
        tx.run(link_query, t_id=node.id, d_id=node.database_id)

    def _merge_queue(self, tx, node: QueueNode):
        query = """
        MERGE (q:Queue {id: $id})
        SET q.name = $name,
            q.broker_type = $broker_type,
            q.consumer_count = $consumer_count
        """
        tx.run(query, id=node.id, name=node.name, broker_type=node.broker_type,
               consumer_count=node.consumer_count)

    def _merge_function(self, tx, node: FunctionNode):
        query = """
        MERGE (f:Function {id: $id})
        SET f.name = $name,
            f.service_id = $service_id,
            f.source_file = $source_file,
            f.start_line = $start_line,
            f.end_line = $end_line,
            f.repo_name = coalesce($repo_name, f.repo_name),
            f.branch = coalesce($branch, f.branch),
            f.commit_sha = coalesce($commit_sha, f.commit_sha)
        """
        tx.run(query, id=node.id, name=node.name, service_id=node.service_id,
               source_file=node.source_file, start_line=node.start_line, end_line=node.end_line,
               repo_name=node.repo_name, branch=node.branch, commit_sha=node.commit_sha)
               
        # Wire it to the parent service
        link_query = """
        MATCH (s:Service {id: $s_id})
        MATCH (f:Function {id: $f_id})
        MERGE (s)-[:HAS_FUNCTION]->(f)
        """
        tx.run(link_query, s_id=node.service_id, f_id=node.id)

    def _merge_edge(self, tx, edge: GraphEdge):
        # We use apoc.create.relationship if dynamic relationship types are needed,
        # but in standard cypher we must construct the string for the rel type securely
        # Since EdgeType is an Enum with safe strings, we can string-format the type.
        
        rel_type = edge.edge_type.value
        query = f"""
        MATCH (source {{id: $source_id}})
        MATCH (target {{id: $target_id}})
        MERGE (source)-[r:{rel_type}]->(target)
        SET r.source_file = $source_file,
            r.source_line = $source_line,
            r.pattern = $pattern,
            r.repo_name = coalesce($repo_name, r.repo_name),
            r.branch = coalesce($branch, r.branch),
            r.commit_sha = coalesce($commit_sha, r.commit_sha)
        """
        tx.run(query, source_id=edge.source_id, target_id=edge.target_id,
               source_file=edge.source_file or "", source_line=edge.source_line or 0,
               pattern=edge.metadata.get("pattern", ""),
               repo_name=edge.repo_name, branch=edge.branch, commit_sha=edge.commit_sha)
               
        # Service-level auto-aggregation: if source is a function, mirror edge to the parent service
        agg_query = f"""
        MATCH (s:Service)-[:HAS_FUNCTION]->(f:Function {{id: $source_id}})
        MATCH (target {{id: $target_id}})
        MERGE (s)-[:{rel_type}]->(target)
        """
        tx.run(agg_query, source_id=edge.source_id, target_id=edge.target_id)

    def add_iac_parsed(self, iac: IaCParseResult) -> None:
        """Ingest nodes discovered from IaC manifests."""
        with self.driver.session() as session:
            for svc in iac.services:
                session.execute_write(self._merge_service, svc)
            for db in iac.databases:
                session.execute_write(self._merge_database, db)
                
                # Auto-generate a default table for the database so edges have a target
                table = TableNode(
                    id=f"table_{db.id}_default",
                    name=f"{db.name}_records",
                    database_id=db.id,
                )
                session.execute_write(self._merge_table, table)
            for queue in iac.queues:
                session.execute_write(self._merge_queue, queue)

    def add_parsed_service(self, parsed: ParsedService) -> None:
        """Ingest edges and dynamically create nodes discovered from AST analysis."""
        with self.driver.session() as session:
            session.execute_write(self._sync_parsed_service_tx, parsed)

    def _sync_parsed_service_tx(self, tx, parsed) -> None:
        # 1. Stale Edge Cleanup (Wipe scope for this service atomically)
        tx.run("""
        MATCH (s:Service {id: $service_id})-[:HAS_FUNCTION]->(f:Function)
        DETACH DELETE f
        """, service_id=parsed.service_id)
        
        tx.run("""
        MATCH (s:Service {id: $service_id})-[r:WRITES_TO|READS_FROM|USES_LOCK|USES_TRANSACTION|CALLS]->()
        DELETE r
        """, service_id=parsed.service_id)

        # 2. Ensure Service Node
        query_svc = """
        MERGE (s:Service {id: $id})
        ON CREATE SET s.name = $name,
                      s.language = $language,
                      s.replica_count = 1,
                      s.source_file = $source_file,
                      s.repo_name = $repo_name,
                      s.branch = $branch,
                      s.commit_sha = $commit_sha
        ON MATCH SET s.repo_name = coalesce($repo_name, s.repo_name),
                     s.branch = coalesce($branch, s.branch),
                     s.commit_sha = coalesce($commit_sha, s.commit_sha)
        """
        tx.run(query_svc, id=parsed.service_id, name=parsed.service_name,
                    language=parsed.language, source_file=parsed.source_file,
                    repo_name=parsed.repo_name, branch=parsed.branch, commit_sha=parsed.commit_sha)

        # 3. Add functions
        for func in parsed.functions:
            self._merge_function(tx, func)
            
        # 4. Add edges
        from core.graph.target_resolver import resolve_target
        for edge in parsed.edges:
            resolved_target = resolve_target(edge.target_id, edge.metadata or {})
            edge.target_id = resolved_target

            query_target = """
            MERGE (t:Table {id: $id})
            ON CREATE SET t.name = $name
            ON MATCH SET t.name = coalesce(t.name, $name)
            """
            hint = edge.metadata.get("target_hint")
            db_id = edge.metadata.get("database_id")
            tx.run(query_target, id=edge.target_id, name=hint)
            
            if db_id:
                link_query = """
                MATCH (t:Table {id: $t_id})
                MATCH (d:Database {id: $d_id})
                MERGE (t)-[:BELONGS_TO]->(d)
                """
                tx.run(link_query, t_id=edge.target_id, d_id=db_id)
                
            self._merge_edge(tx, edge)
