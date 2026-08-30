"""
core/ingestion/iac_parser.py
────────────────────────────────────────────────────────────────────────────
Infrastructure-as-Code Parser.

Extracts container topology, replica counts, and service boundaries from:
  • Docker Compose (docker-compose.yml / docker-compose.yaml)
  • Kubernetes Deployment / Service manifests (.yaml / .yml)
  • Terraform HCL2 files (.tf)  [basic resource extraction]

Returns:
  • List of ServiceNode (with replica_count populated)
  • List of DatabaseNode
  • List of QueueNode
  • List of GraphEdge (network policy edges, if available)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

from core.schema import (
    ServiceNode, DatabaseNode, QueueNode, GraphEdge,
    EdgeType, DbIsolationLevel,
)

# python-hcl2 is optional — only needed for Terraform files
try:
    import hcl2
    _HCL2_AVAILABLE = True
except ImportError:
    _HCL2_AVAILABLE = False


# ─── Known DB/Queue image fingerprints ────────────────────────────────────────

_DB_IMAGE_FINGERPRINTS: dict[str, str] = {
    "postgres": "postgresql",
    "postgis":  "postgresql",
    "mysql":    "mysql",
    "mariadb":  "mysql",
    "mongo":    "mongodb",
    "redis":    "redis",
    "cassandra": "cassandra",
    "cockroach": "cockroachdb",
}

_QUEUE_IMAGE_FINGERPRINTS: dict[str, str] = {
    "kafka":     "kafka",
    "rabbitmq":  "rabbitmq",
    "nats":      "nats",
    "activemq":  "activemq",
    "pulsar":    "pulsar",
}

_DB_ENV_ISOLATION_MAP: dict[str, DbIsolationLevel] = {
    "SERIALIZABLE":     DbIsolationLevel.SERIALIZABLE,
    "REPEATABLE READ":  DbIsolationLevel.REPEATABLE_READ,
    "READ COMMITTED":   DbIsolationLevel.READ_COMMITTED,
    "READ UNCOMMITTED": DbIsolationLevel.READ_UNCOMMITTED,
}


# ─── Parse Result ─────────────────────────────────────────────────────────────

class IaCParseResult:
    """Collected nodes from IaC file parsing."""

    def __init__(self) -> None:
        self.services:  list[ServiceNode]  = []
        self.databases: list[DatabaseNode] = []
        self.queues:    list[QueueNode]    = []
        self.edges:     list[GraphEdge]    = []
        self.warnings:  list[str]          = []
        self.service_to_db: dict[str, str] = {}

    def merge(self, other: "IaCParseResult") -> None:
        """Merge another result into this one (for multi-file parsing)."""
        self.services.extend(other.services)
        self.databases.extend(other.databases)
        self.queues.extend(other.queues)
        self.edges.extend(other.edges)
        self.warnings.extend(other.warnings)
        self.service_to_db.update(other.service_to_db)


# ─── Main Parser ──────────────────────────────────────────────────────────────

class IaCParser:
    """
    Parses IaC files and returns structured node/edge lists.

    Usage:
        parser = IaCParser()
        parser.load_overrides(Path(".omnigraph.yml"))
        result = parser.parse_file(Path("docker-compose.yml"))
        
    .omnigraph.yml Schema Note:
    - `database_identities`: Maps a raw host string (e.g. 'postgres') OR a fallback ID 
      (e.g. 'db_unknown_svc_order') to a canonical identity. 
      NOTE: While this section is technically "global", mapping a fallback ID here 
      is effectively a service-scoped override since fallback IDs are tightly bound 
      to a single service. Mapping literal hostnames here truly applies globally.
    - `overrides_by_service`: Maps a specific service_id to an explicit db_ref -> identity.
    """

    def __init__(self):
        self.overrides = {}

    def load_overrides(self, override_path: Path) -> None:
        if override_path.exists():
            import yaml
            try:
                with override_path.open("r", encoding="utf-8") as f:
                    self.overrides = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Warning: Failed to parse {override_path}: {e}")

    def parse_file(self, file_path: Path) -> IaCParseResult:
        """Auto-detects file type and routes to the correct parser."""
        suffix = file_path.suffix.lower()
        name   = file_path.name.lower()

        if suffix in (".yml", ".yaml"):
            return self._parse_yaml_file(file_path)
        elif suffix == ".tf":
            return self._parse_terraform_file(file_path)
        else:
            result = IaCParseResult()
            result.warnings.append(f"Unsupported IaC file type: {file_path}")
            return result

    def parse_directory(self, directory: Path) -> IaCParseResult:
        """Parse all IaC files in a directory, merging results."""
        merged = IaCParseResult()
        patterns = ["**/*.yml", "**/*.yaml", "**/*.tf"]
        seen = set()
        for pat in patterns:
            for path in sorted(directory.glob(pat)):
                if path in seen:
                    continue
                seen.add(path)
                merged.merge(self.parse_file(path))
        return merged

    # ─── YAML Router ──────────────────────────────────────────────────────────

    def _parse_yaml_file(self, file_path: Path) -> IaCParseResult:
        try:
            with file_path.open("r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
        except yaml.YAMLError as e:
            result = IaCParseResult()
            result.warnings.append(f"YAML parse error in {file_path}: {e}")
            return result

        merged = IaCParseResult()
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if self._is_docker_compose(doc):
                merged.merge(self._parse_docker_compose(doc, str(file_path)))
            elif self._is_k8s_manifest(doc):
                merged.merge(self._parse_k8s_manifest(doc, str(file_path)))

        return merged

    @staticmethod
    def _is_docker_compose(doc: dict) -> bool:
        return "services" in doc and "version" in doc or "services" in doc

    @staticmethod
    def _is_k8s_manifest(doc: dict) -> bool:
        return "apiVersion" in doc and "kind" in doc

    # ─── Docker Compose Parser ────────────────────────────────────────────────

    def _extract_db_identity(self, env_vars, fallback_id: str, svc_id: str) -> str:
        if not isinstance(env_vars, dict):
            if isinstance(env_vars, list):
                env_dict = {}
                for item in env_vars:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        env_dict[k] = v
                env_vars = env_dict
            else:
                env_vars = {}

        if "OMNIGRAPH_DB_IDENTITY" in env_vars:
            return f"db_{env_vars['OMNIGRAPH_DB_IDENTITY']}"

        raw_ref = fallback_id
        for key, val in env_vars.items():
            k = key.upper()
            if "DATABASE_URL" in k or "DB_HOST" in k or "POSTGRES_HOST" in k:
                val = str(val).strip()
                host = val.lower()
                if "://" in host:
                    parts = host.split("://")[1]
                    if "@" in parts:
                        parts = parts.split("@")[1]
                    host = parts.split(":")[0].split("/")[0]
                raw_ref = host
                break

        # If host is empty (e.g. from a k8s valueFrom secret), or is a pure variable injection,
        # it is unresolvable. Treat its raw_ref as the fallback_id so users can override it by fallback_id.
        if not raw_ref or raw_ref.startswith("$") or raw_ref.startswith("${"):
            raw_ref = fallback_id

        # 1. Check Service-Scoped Override First
        svc_overrides = self.overrides.get("overrides_by_service", {}).get(svc_id, {})
        if svc_overrides.get("db_ref") == raw_ref:
            return svc_overrides.get("identity")

        # 2. Check Global Identity Override
        global_identities = self.overrides.get("database_identities", {})
        if raw_ref in global_identities:
            val = global_identities[raw_ref]
            return val.get("identity") if isinstance(val, dict) else val

        # 3. No override matched. Proceed with literal / fail-safe logic.
        if raw_ref == fallback_id:
            return fallback_id
        
        # (We leave explicit identical secrets like `vault:db` alone per design rules)
        return f"db_{raw_ref}"

    def _parse_docker_compose(self, doc: dict, source_file: str) -> IaCParseResult:
        result = IaCParseResult()
        services_block = doc.get("services", {})
        if not isinstance(services_block, dict):
            return result

        for svc_name, svc_config in services_block.items():
            if not isinstance(svc_config, dict):
                continue

            image = svc_config.get("image", "")
            db_type   = self._fingerprint_image(image, _DB_IMAGE_FINGERPRINTS)
            queue_type = self._fingerprint_image(image, _QUEUE_IMAGE_FINGERPRINTS)

            # Replica count — docker-compose deploy block
            deploy_block = svc_config.get("deploy", {})
            replicas = 1
            if isinstance(deploy_block, dict):
                replicas = int(deploy_block.get("replicas", 1))

            svc_id = f"svc_{svc_name.lower().replace('-', '_')}"

            if db_type:
                isolation = self._extract_isolation_from_env(svc_config.get("environment", {}))
                # For a DB container, its service name is its network identity
                db_id = f"db_{svc_name}"
                result.databases.append(DatabaseNode(
                    id=db_id,
                    name=svc_name,
                    db_type=db_type,
                    isolation_level=isolation,
                ))
            elif queue_type:
                result.queues.append(QueueNode(
                    id=f"queue_{svc_name}",
                    name=svc_name,
                    broker_type=queue_type,
                ))
            else:
                # Detect language from environment or build context
                language = self._detect_language_from_config(svc_config)
                
                # Extract the DB identity this app connects to
                env_vars = svc_config.get("environment", {})
                fallback = f"db_unknown_{svc_id}"
                db_id = self._extract_db_identity(env_vars, fallback, svc_id)
                result.service_to_db[svc_id] = db_id
                
                result.services.append(ServiceNode(
                    id=svc_id,
                    name=svc_name,
                    language=language,
                    replica_count=replicas,
                    source_file=source_file,
                ))

        return result

    # ─── Kubernetes Parser ─────────────────────────────────────────────────────

    def _parse_k8s_manifest(self, doc: dict, source_file: str) -> IaCParseResult:
        result = IaCParseResult()
        kind = doc.get("kind", "")

        if kind == "Deployment":
            result.merge(self._parse_k8s_deployment(doc, source_file))
        elif kind == "StatefulSet":
            result.merge(self._parse_k8s_deployment(doc, source_file))
        # Services, ConfigMaps etc. skipped for Phase 1

        return result

    def _parse_k8s_deployment(self, doc: dict, source_file: str) -> IaCParseResult:
        result = IaCParseResult()
        metadata = doc.get("metadata", {})
        spec     = doc.get("spec", {})

        name     = metadata.get("name", "unknown")
        replicas = int(spec.get("replicas", 1))

        # Walk containers to find images
        template = spec.get("template", {})
        pod_spec = template.get("spec", {})
        containers = pod_spec.get("containers", [])

        for container in containers:
            if not isinstance(container, dict):
                continue
            image      = container.get("image", "")
            db_type    = self._fingerprint_image(image, _DB_IMAGE_FINGERPRINTS)
            queue_type = self._fingerprint_image(image, _QUEUE_IMAGE_FINGERPRINTS)
            env        = container.get("env", [])

            svc_id = f"svc_{name.lower().replace('-', '_')}"

            if db_type:
                db_id = f"db_{name}"
                result.databases.append(DatabaseNode(
                    id=db_id,
                    name=name,
                    db_type=db_type,
                    isolation_level=DbIsolationLevel.READ_COMMITTED
                ))
            elif queue_type:
                result.queues.append(QueueNode(
                    id=f"queue_{name}",
                    name=name,
                    broker_type=queue_type,
                ))
            else:
                language = self._detect_language_from_env_list(env)
                
                # Convert k8s env list to dict for extraction
                env_dict = {}
                for e in env:
                    if isinstance(e, dict) and "name" in e and "value" in e:
                        env_dict[e["name"]] = e["value"]
                        
                fallback = f"db_unknown_{svc_id}"
                db_id = self._extract_db_identity(env_dict, fallback, svc_id)
                result.service_to_db[svc_id] = db_id
                
                result.services.append(ServiceNode(
                    id=svc_id,
                    name=name,
                    language=language,
                    replica_count=replicas,
                    source_file=source_file,
                ))

        return result

    # ─── Terraform Parser (Basic) ──────────────────────────────────────────────

    def _parse_terraform_file(self, file_path: Path) -> IaCParseResult:
        result = IaCParseResult()
        if not _HCL2_AVAILABLE:
            result.warnings.append(
                f"python-hcl2 not installed. Skipping Terraform file: {file_path}. "
                "Install with: pip install python-hcl2"
            )
            return result

        try:
            with file_path.open("r", encoding="utf-8") as f:
                tf_data = hcl2.load(f)
        except Exception as e:
            result.warnings.append(f"HCL2 parse error in {file_path}: {e}")
            return result

        # Extract ECS task definitions or Kubernetes node pools for replica counts
        for resource_type, resources in tf_data.get("resource", {}).items():
            for resource_name, resource_config in resources.items():
                if "desired_count" in resource_config:  # ECS
                    replicas = int(resource_config.get("desired_count", 1))
                    result.services.append(ServiceNode(
                        id=f"svc_{resource_name}",
                        name=resource_name,
                        language="unknown",
                        replica_count=replicas,
                        source_file=str(file_path),
                    ))
                elif "node_count" in resource_config:   # GKE / AKS node pools
                    # Not a service per se, but informational
                    result.warnings.append(
                        f"Terraform node pool '{resource_name}' has "
                        f"count={resource_config.get('node_count')} — manual review recommended."
                    )

        return result

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint_image(image: str, fingerprints: dict[str, str]) -> Optional[str]:
        """Match a container image string against known fingerprints."""
        image_lower = image.lower()
        for keyword, db_type in fingerprints.items():
            if keyword in image_lower:
                return db_type
        return None

    @staticmethod
    def _extract_isolation_from_env(env: Any) -> DbIsolationLevel:
        """Extract DB isolation level from docker-compose environment block."""
        if isinstance(env, dict):
            for key, val in env.items():
                if "isolation" in key.lower() and isinstance(val, str):
                    upper = val.upper()
                    for name, level in _DB_ENV_ISOLATION_MAP.items():
                        if name in upper:
                            return level
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "ISOLATION" in item.upper():
                    for name, level in _DB_ENV_ISOLATION_MAP.items():
                        if name in item.upper():
                            return level
        return DbIsolationLevel.UNKNOWN

    @staticmethod
    def _extract_isolation_from_k8s_env(env: list) -> DbIsolationLevel:
        """Extract DB isolation level from K8s env list [{name: X, value: Y}]."""
        for item in env:
            if not isinstance(item, dict):
                continue
            key = item.get("name", "")
            val = item.get("value", "")
            if "isolation" in key.lower() and isinstance(val, str):
                for name, level in _DB_ENV_ISOLATION_MAP.items():
                    if name in val.upper():
                        return level
        return DbIsolationLevel.UNKNOWN

    @staticmethod
    def _detect_language_from_config(svc_config: dict) -> str:
        """Best-effort language detection from Docker Compose service config."""
        # Check build context / Dockerfile hints
        build = svc_config.get("build", "")
        if isinstance(build, dict):
            dockerfile = build.get("dockerfile", "")
            if "python" in dockerfile.lower() or "django" in dockerfile.lower():
                return "python"
            if "node" in dockerfile.lower() or "ts" in dockerfile.lower():
                return "javascript"
        # Check environment variables
        env = svc_config.get("environment", {})
        env_str = str(env).lower()
        if "python" in env_str or "django" in env_str or "flask" in env_str:
            return "python"
        if "node" in env_str or "npm" in env_str or "nest" in env_str:
            return "javascript"
        return "unknown"

    @staticmethod
    def _detect_language_from_env_list(env: list) -> str:
        """Detect language from K8s env list."""
        for item in env:
            if not isinstance(item, dict):
                continue
            val = str(item.get("value", "")).lower()
            if "python" in val or "django" in val:
                return "python"
            if "node" in val or "npm" in val:
                return "javascript"
        return "unknown"
