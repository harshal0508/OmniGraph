"""
core/graph/builder.py
─────────────────────────────────────────────────────────────────────────────
NetworkX Graph Builder.

Ingests ParsedService results (from AST parser) and IaCParseResult (from IaC
parser) and constructs a unified directed topology graph.

Graph Structure:
  • Nodes — ServiceNode, DatabaseNode, TableNode, QueueNode, MutexNode
  • Edges — EdgeType enum values as the "type" attribute

The graph is a NetworkX MultiDiGraph (multiple edges between same node pair
are valid — a service can both READ_FROM and WRITE_TO the same table).
"""

from __future__ import annotations

import uuid
from typing import Any

import networkx as nx

from core.schema import (
    NodeType, EdgeType, DbIsolationLevel,
    ServiceNode, DatabaseNode, TableNode, QueueNode, MutexNode,
    GraphEdge,
)
from core.ingestion.ast_parser import ParsedService
from core.ingestion.iac_parser import IaCParseResult


# ─── Node Attribute Keys ──────────────────────────────────────────────────────
#  All nodes carry a "node_type" attribute so rules can filter cleanly.

def _service_attrs(node: ServiceNode) -> dict[str, Any]:
    return {
        "node_type":     NodeType.SERVICE.value,
        "name":          node.name,
        "language":      node.language,
        "replica_count": node.replica_count,
        "source_file":   node.source_file or "",
    }

def _database_attrs(node: DatabaseNode) -> dict[str, Any]:
    return {
        "node_type":        NodeType.DATABASE.value,
        "name":             node.name,
        "db_type":          node.db_type,
        "isolation_level":  node.isolation_level.value,
    }

def _table_attrs(node: TableNode) -> dict[str, Any]:
    return {
        "node_type":   NodeType.TABLE.value,
        "name":        node.name,
        "database_id": node.database_id,
    }

def _queue_attrs(node: QueueNode) -> dict[str, Any]:
    return {
        "node_type":      NodeType.QUEUE.value,
        "name":           node.name,
        "broker_type":    node.broker_type,
        "consumer_count": node.consumer_count,
    }

def _mutex_attrs(node: MutexNode) -> dict[str, Any]:
    return {
        "node_type":  NodeType.MUTEX.value,
        "name":       node.name,
        "mutex_type": node.mutex_type,
    }


# ─── Graph Builder ────────────────────────────────────────────────────────────

class GraphBuilder:
    """
    Builds the unified OmniGraph topology graph from parsed data.

    Usage:
        builder = GraphBuilder()
        builder.add_iac_result(iac_result)
        builder.add_parsed_service(parsed_service_a)
        builder.add_parsed_service(parsed_service_b)
        graph = builder.build()
    """

    def __init__(self) -> None:
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._service_ids: set[str] = set()
        self._database_ids: set[str] = set()
        self._queue_ids: set[str] = set()
        self._mutex_ids: set[str] = set()
        # Map from service_id → replica_count (from IaC)
        self._replica_map: dict[str, int] = {}

    # ─── Ingest IaC ───────────────────────────────────────────────────────────

    def add_iac_result(self, iac: IaCParseResult) -> None:
        """Add all nodes discovered from IaC files to the graph."""
        for svc in iac.services:
            self._add_service_node(svc)
            self._replica_map[svc.id] = svc.replica_count

        for db in iac.databases:
            self._add_database_node(db)
            # Auto-create a generic table node for prototype
            table = TableNode(
                id=f"table_{db.id}_default",
                name=f"{db.name}_records",
                database_id=db.id,
            )
            self._add_table_node(table)

        for queue in iac.queues:
            self._add_queue_node(queue)

    # ─── Ingest AST Parsed Service ────────────────────────────────────────────

    def add_parsed_service(self, parsed: ParsedService) -> None:
        """
        Add a parsed service's edges to the graph.
        If the service node doesn't exist yet (not in IaC), create it.
        """
        # Ensure the service node exists
        if parsed.service_id not in self._service_ids:
            svc_node = ServiceNode(
                id=parsed.service_id,
                name=parsed.service_name,
                language=parsed.language,
                replica_count=self._replica_map.get(parsed.service_id, 1),
                source_file=parsed.source_file,
            )
            self._add_service_node(svc_node)

        # Update replica count from IaC map if available
        if parsed.service_id in self._replica_map:
            self._graph.nodes[parsed.service_id]["replica_count"] = (
                self._replica_map[parsed.service_id]
            )

        # Add lock/transaction self-loop markers
        if parsed.has_lock:
            mutex_id = f"mutex_{parsed.service_id}"
            if mutex_id not in self._mutex_ids:
                self._add_mutex_node(MutexNode(
                    id=mutex_id,
                    name=f"{parsed.service_name}_lock",
                    mutex_type="detected",
                ))
            self._add_edge(GraphEdge(
                source_id=parsed.service_id,
                target_id=mutex_id,
                edge_type=EdgeType.USES_LOCK,
                source_file=parsed.source_file,
            ))

        if parsed.has_transaction:
            # Mark the service node itself as transaction-protected
            self._graph.nodes[parsed.service_id]["has_transaction"] = True

        # Add all detected edges
        for edge in parsed.edges:
            resolved_target = self._resolve_edge_target(edge, parsed)
            if resolved_target:
                resolved_edge = GraphEdge(
                    source_id=edge.source_id,
                    target_id=resolved_target,
                    edge_type=edge.edge_type,
                    source_file=edge.source_file,
                    source_line=edge.source_line,
                    metadata=edge.metadata,
                )
                self._add_edge(resolved_edge)

    def _resolve_edge_target(self, edge: GraphEdge, parsed: ParsedService) -> str | None:
        """
        Resolve unresolved table targets to actual graph node IDs.

        In Phase 1 (prototype), we use a simple heuristic:
          - If the repo has exactly one database, all WRITES_TO point to it.
          - Otherwise, we create an "unknown_table" placeholder node.
        """
        if edge.target_id not in ("__UNRESOLVED_TABLE__", "__SQL_WRITE_TARGET__", "__SQL_READ_TARGET__"):
            return edge.target_id if edge.target_id in self._graph else None

        # Find all table nodes in graph
        table_nodes = [
            n for n, attr in self._graph.nodes(data=True)
            if attr.get("node_type") == NodeType.TABLE.value
        ]
        if len(table_nodes) == 1:
            return table_nodes[0]
        if table_nodes:
            # Multiple tables — use first DB's default table
            return table_nodes[0]

        # No tables registered — create a synthetic placeholder
        placeholder_id = "table_unknown_shared"
        if placeholder_id not in self._graph:
            self._graph.add_node(
                placeholder_id,
                node_type=NodeType.TABLE.value,
                name="unknown_shared_table",
                database_id="db_unknown",
            )
        return placeholder_id

    # ─── Build ────────────────────────────────────────────────────────────────

    def build(self) -> nx.MultiDiGraph:
        """Finalise and return the complete topology graph."""
        return self._graph

    def summary(self) -> dict[str, int]:
        """Returns a summary of graph contents."""
        node_types: dict[str, int] = {}
        for _, attrs in self._graph.nodes(data=True):
            nt = attrs.get("node_type", "Unknown")
            node_types[nt] = node_types.get(nt, 0) + 1

        edge_types: dict[str, int] = {}
        for _, _, attrs in self._graph.edges(data=True):
            et = attrs.get("edge_type", "Unknown")
            edge_types[et] = edge_types.get(et, 0) + 1

        return {
            "total_nodes": self._graph.number_of_nodes(),
            "total_edges": self._graph.number_of_edges(),
            "node_types":  node_types,
            "edge_types":  edge_types,
        }

    # ─── Private Node Adders ──────────────────────────────────────────────────

    def _add_service_node(self, node: ServiceNode) -> None:
        self._graph.add_node(node.id, **_service_attrs(node))
        self._service_ids.add(node.id)

    def _add_database_node(self, node: DatabaseNode) -> None:
        self._graph.add_node(node.id, **_database_attrs(node))
        self._database_ids.add(node.id)

    def _add_table_node(self, node: TableNode) -> None:
        self._graph.add_node(node.id, **_table_attrs(node))

    def _add_queue_node(self, node: QueueNode) -> None:
        self._graph.add_node(node.id, **_queue_attrs(node))
        self._queue_ids.add(node.id)

    def _add_mutex_node(self, node: MutexNode) -> None:
        self._graph.add_node(node.id, **_mutex_attrs(node))
        self._mutex_ids.add(node.id)

    def _add_edge(self, edge: GraphEdge) -> None:
        """Add a directed edge to the graph. Ensures both nodes exist."""
        if edge.source_id not in self._graph:
            self._graph.add_node(edge.source_id, node_type="Unknown", name=edge.source_id)
        if edge.target_id not in self._graph:
            self._graph.add_node(edge.target_id, node_type="Unknown", name=edge.target_id)

        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            edge_type=edge.edge_type.value,
            source_file=edge.source_file or "",
            source_line=edge.source_line or 0,
            **edge.metadata,
        )
