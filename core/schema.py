"""
core/schema.py
──────────────
Central type definitions for the OmniGraph topology graph.
All node types, edge types, and finding models live here.
Nothing in this module has external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ─── Node Types ───────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    SERVICE   = "Service"
    ROUTE     = "Route"
    FUNCTION  = "Function"
    DATABASE  = "Database"
    TABLE     = "Table"
    QUEUE     = "Queue"
    MUTEX     = "Mutex"
    SAGA      = "Saga"


# ─── Edge Types ───────────────────────────────────────────────────────────────

class EdgeType(str, Enum):
    CALLS             = "CALLS"
    WRITES_TO         = "WRITES_TO"
    READS_FROM        = "READS_FROM"
    USES_LOCK         = "USES_LOCK"
    USES_TRANSACTION  = "USES_TRANSACTION"
    PUBLISHES_TO      = "PUBLISHES_TO"
    CONSUMED_BY       = "CONSUMED_BY"
    DELEGATES_TO      = "DELEGATES_TO"
    TOCTOU_VULNERABLE = "TOCTOU_VULNERABLE"


# ─── DB Isolation ─────────────────────────────────────────────────────────────

class DbIsolationLevel(str, Enum):
    READ_UNCOMMITTED = "READ_UNCOMMITTED"
    READ_COMMITTED   = "READ_COMMITTED"
    REPEATABLE_READ  = "REPEATABLE_READ"
    SERIALIZABLE     = "SERIALIZABLE"
    UNKNOWN          = "UNKNOWN"


# ─── Collision Severity ───────────────────────────────────────────────────────

class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING  = "WARNING"
    INFO     = "INFO"


# ─── Node Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ServiceNode:
    id: str
    name: str
    language: str
    replica_count: int = 1
    source_file: Optional[str] = None
    node_type: NodeType = NodeType.SERVICE
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None


@dataclass
class DatabaseNode:
    id: str
    name: str
    db_type: str
    isolation_level: DbIsolationLevel = DbIsolationLevel.UNKNOWN
    node_type: NodeType = NodeType.DATABASE


@dataclass
class TableNode:
    id: str
    name: str
    database_id: str
    node_type: NodeType = NodeType.TABLE


@dataclass
class QueueNode:
    id: str
    name: str
    broker_type: str
    consumer_count: int = 1
    node_type: NodeType = NodeType.QUEUE


@dataclass
class MutexNode:
    id: str
    name: str
    mutex_type: str
    node_type: NodeType = NodeType.MUTEX


@dataclass
class FunctionNode:
    id: str
    name: str
    service_id: str
    source_file: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    node_type: NodeType = NodeType.FUNCTION
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None


# ─── Edge Dataclass ───────────────────────────────────────────────────────────

@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    edge_type: EdgeType
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None


# ─── Finding / Report Models ──────────────────────────────────────────────────

@dataclass
class EvidencePath:
    file: str
    line: Optional[int]
    description: str

    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"{self.file}{loc} — {self.description}"


@dataclass
class CollisionFinding:
    collision_type: str
    actor_1_id: str
    actor_2_id: str
    shared_target_id: str
    atomic_protection: bool
    confidence: float
    evidence: list
    severity: Severity = Severity.CRITICAL
    suppressed: bool = False
    suppression_reason: Optional[str] = None
    remediation_hint: Optional[str] = None

    @property
    def is_actionable(self) -> bool:
        return not self.suppressed and self.confidence >= 0.65

    def to_scrubbed_dict(self) -> dict:
        return {
            "collision_type": self.collision_type,
            "actor_1": self.actor_1_id,
            "actor_2": self.actor_2_id,
            "shared_target": self.shared_target_id,
            "atomic_protection": self.atomic_protection,
            "confidence": round(self.confidence, 2),
            "severity": self.severity.value,
        }


@dataclass
class AnalysisReport:
    scan_id: str
    source_path: str
    total_services: int
    total_edges: int
    findings: list
    warnings: list = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL and f.is_actionable)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING and f.is_actionable)
