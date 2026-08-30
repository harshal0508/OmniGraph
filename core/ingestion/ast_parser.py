"""
core/ingestion/ast_parser.py
─────────────────────────────────────────────────────────────────────────────
Multi-language AST parser using Tree-sitter.

Parses Python and JavaScript/TypeScript source files and extracts:
  • Service-level functions / route handlers
  • ORM write / read method calls  → WRITES_TO / READS_FROM edges
  • Lock acquisition patterns       → USES_LOCK edges
  • Transaction patterns            → USES_TRANSACTION edges
  • Raw SQL string analysis         → WRITES_TO / READS_FROM edges

Returns a list of GraphEdge objects for the graph builder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.schema import EdgeType, GraphEdge, EvidencePath, FunctionNode
from core.ingestion.orm_semantic_map import (
    resolve_method_to_edge,
    is_sql_write,
    is_sql_read,
)

# Tree-sitter is imported lazily so the module can be imported
# even if tree-sitter wheels are not installed (useful for tests).
try:
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
    from tree_sitter import Language, Parser, Node
    _TREE_SITTER_AVAILABLE = True
except ImportError:
    _TREE_SITTER_AVAILABLE = False


# ─── Language Loader ──────────────────────────────────────────────────────────

def _get_parser(language: str) -> "Parser":
    """Returns a configured Tree-sitter Parser for the given language."""
    if not _TREE_SITTER_AVAILABLE:
        raise RuntimeError(
            "tree-sitter packages not installed. "
            "Run: pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript"
        )
    lang_map = {
        "python":     Language(tspython.language()),
        "javascript": Language(tsjavascript.language()),
        "typescript": Language(tstypescript.language_typescript()),
    }
    if language not in lang_map:
        raise ValueError(f"Unsupported language: {language}. Supported: {list(lang_map)}")
    parser = Parser(lang_map[language])
    return parser


# ─── Parse Result ─────────────────────────────────────────────────────────────

@dataclass
class ParsedService:
    """Represents a parsed source file contributing to the graph."""
    service_id: str          # Unique ID (e.g. "svc_order_service")
    service_name: str        # Human name (e.g. "order_service")
    language: str
    source_file: str
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    functions: list[FunctionNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Tables / datastores referenced — inferred from ORM calls
    referenced_tables: set[str] = field(default_factory=set)
    has_lock: bool = False
    has_transaction: bool = False
    local_calls: list[tuple[str, str, int, bool, str|None, str]] = field(default_factory=list)
    imports: list[tuple[str, str, str]] = field(default_factory=list)  # (caller, callee, line, is_self, current_class)  # (caller_id, callee_name, line)


# ─── Core Parser ──────────────────────────────────────────────────────────────

class ASTParser:
    """
    Parses a source file into a ParsedService with graph edges.

    Usage:
        parser = ASTParser()
        result = parser.parse_file(Path("services/order.py"), service_id="svc_order")
    """

    def parse_file(
        self,
        file_path: Path,
        service_id: str,
        service_name: Optional[str] = None,
    ) -> ParsedService:
        """Parse a single source file and return a ParsedService."""
        language = self._detect_language(file_path)
        name = service_name or file_path.stem

        result = ParsedService(
            service_id=service_id,
            service_name=name,
            language=language,
            source_file=str(file_path),
        )

        source = file_path.read_bytes()


        # Extract imports via regex
        src_str = source.decode("utf-8", errors="ignore")
        import_pattern = re.compile(r'from\s+([a-zA-Z0-9_\.]+)\s+import\s+([a-zA-Z0-9_, ]+)')
        for match in import_pattern.finditer(src_str):
            module = match.group(1).split('.')[-1]
            names = [n.strip() for n in match.group(2).split(',')]
            for name in names:
                result.imports.append((result.source_file, module, name))
                
        js_import_pattern = re.compile(r'import\s+{([^}]+)}\s+from\s+[\'"]([a-zA-Z0-9_\.\/]+)[\'"]')
        for match in js_import_pattern.finditer(src_str):
            module = match.group(2).split('/')[-1].replace('.js', '').replace('.ts', '')
            names = [n.strip() for n in match.group(1).split(',')]
            for name in names:
                result.imports.append((result.source_file, module, name))
        if _TREE_SITTER_AVAILABLE:
            self._parse_with_tree_sitter(source, language, result)
        else:
            # Fallback: regex-based extraction for dev/test environments
            self._parse_with_regex(source.decode("utf-8", errors="ignore"), language, result)

        # Resolve local CALLS edges
        # Map: (module_name, func_name) -> func_id
        from pathlib import Path
        func_map = {(Path(f.source_file).stem, f.name): f.id for f in result.functions}
        
        # Build import map: source_file -> {imported_name: module_name}
        import_map = {}
        for src_file, mod, name in result.imports:
            import_map.setdefault(src_file, {})[name] = mod

        for caller_id, callee_name, line, is_self, current_class, module_name in result.local_calls:
            target_name = f"{current_class}.{callee_name}" if (is_self and current_class) else callee_name
            
            target_func_id = func_map.get((module_name, target_name))
            if not target_func_id and not is_self:
                imported_mod = import_map.get(result.source_file, {}).get(callee_name)
                if imported_mod:
                    target_func_id = func_map.get((imported_mod, callee_name))

            if target_func_id:
                result.edges.append(GraphEdge(
                    source_id=caller_id,
                    target_id=target_func_id,
                    edge_type=EdgeType.CALLS,
                    source_file=result.source_file,
                    source_line=line,
                    metadata={"pattern": "direct_call"},
                    repo_name=result.repo_name,
                    branch=result.branch,
                    commit_sha=result.commit_sha
                ))

        return result


    # ─── Language Detection ────────────────────────────────────────────────────

    @staticmethod
    def _detect_language(path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".py": "python",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
        }.get(ext, "python")

    # ─── Tree-sitter Path ──────────────────────────────────────────────────────

    def _parse_with_tree_sitter(
        self,
        source: bytes,
        language: str,
        result: ParsedService,
    ) -> None:
        parser = _get_parser(language)
        tree = parser.parse(source)

        # Walk the tree and extract nodes of interest
        self._walk_tree(tree.root_node, source, language, result)

    def _walk_tree(
        self,
        node: "Node",
        source: bytes,
        language: str,
        result: ParsedService,
        current_func_id: Optional[str] = None,
        current_class: Optional[str] = None,
    ) -> None:
        """Recursively walks the AST and extracts ORM calls, SQL strings, lock/txn patterns."""
        if node.type == "class_definition" or node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                current_class = name_node.text.decode("utf-8")

        if node.type in ("function_definition", "function_declaration", "arrow_function", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node:
                raw_func_name = name_node.text.decode("utf-8")
                func_name = f"{current_class}.{raw_func_name}" if current_class else raw_func_name
                current_func_id = f"func_{result.service_id}_{func_name.replace('.', '_')}"
                line = node.start_point[0] + 1
                result.functions.append(FunctionNode(
                    id=current_func_id,
                    name=func_name,
                    service_id=result.service_id,
                    source_file=result.source_file,
                    start_line=line,
                ))

        elif node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                method_name = None
                table_name = None
                is_self = False
                if func_node.type == "attribute":
                    method_node = func_node.child_by_field_name("attribute")
                    obj_node = func_node.child_by_field_name("value") or func_node.child_by_field_name("object")
                    if method_node:
                        method_name = method_node.text.decode("utf-8", errors="ignore")
                    if obj_node:
                        table_name = obj_node.text.decode("utf-8", errors="ignore")
                        if table_name == "self":
                            is_self = True
                            table_name = None
                        else:
                            table_name = table_name.lower()
                elif func_node.type == "identifier":
                    method_name = func_node.text.decode("utf-8", errors="ignore")
                
                if method_name:
                    lang = result.language
                    line = node.start_point[0] + 1
                    self._resolve_and_add_edge(method_name, lang, result, line, current_func_id, orm_model=table_name)
                    if method_name in ("query", "execute", "raw"):
                        self._extract_sql_from_call(node, source, result, line, current_func_id)
                    if current_func_id:
                        if func_node.type == "identifier" or is_self:
                            from pathlib import Path
                            module_name = Path(result.source_file).stem
                            result.local_calls.append((current_func_id, method_name, line, is_self, current_class, module_name))

        elif node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                method_name = None
                table_name = None
                is_self = False
                if func_node.type == "member_expression":
                    prop_node = func_node.child_by_field_name("property")
                    obj_node = func_node.child_by_field_name("object")
                    if prop_node:
                        method_name = prop_node.text.decode("utf-8", errors="ignore")
                    if obj_node:
                        table_name = obj_node.text.decode("utf-8", errors="ignore")
                        if table_name == "this":
                            is_self = True
                            table_name = None
                        else:
                            table_name = table_name.lower()
                elif func_node.type == "identifier":
                    method_name = func_node.text.decode("utf-8", errors="ignore")

                if method_name:
                    lang = result.language
                    line = node.start_point[0] + 1
                    self._resolve_and_add_edge(method_name, lang, result, line, current_func_id, orm_model=table_name)
                    if method_name in ("query", "execute", "raw"):
                        self._extract_sql_from_call(node, source, result, line, current_func_id)
                    if current_func_id:
                        if func_node.type == "identifier" or is_self:
                            from pathlib import Path
                            module_name = Path(result.source_file).stem
                            result.local_calls.append((current_func_id, method_name, line, is_self, current_class, module_name))

        for child in node.children:
            self._walk_tree(child, source, language, result, current_func_id, current_class)

    def _resolve_and_add_edge(
        self,
        method_name: str,
        language: str,
        result: ParsedService,
        line: int,
        func_id: Optional[str] = None,
        orm_model: Optional[str] = None,
    ) -> None:
        """Resolves a method name to an edge type and appends it to result."""
        edge_type, pattern_name = resolve_method_to_edge(method_name, language)
        if edge_type is None:
            return

        # Determine target - use a placeholder until Target Resolver resolves it
        target = "__UNRESOLVED_TABLE__"
        if edge_type in (EdgeType.WRITES_TO, EdgeType.READS_FROM):
            result.referenced_tables.add(target)

        if edge_type == EdgeType.USES_LOCK:
            result.has_lock = True
        if edge_type == EdgeType.USES_TRANSACTION:
            result.has_transaction = True

        meta = {"pattern": pattern_name, "method": method_name}
        if orm_model:
            meta["orm_model"] = orm_model

        result.edges.append(GraphEdge(
            source_id=func_id or result.service_id,
            target_id=target,
            edge_type=edge_type,
            source_file=result.source_file,
            source_line=line,
            metadata=meta,
        ))

    def _extract_sql_from_call(
        self,
        call_node: "Node",
        source: bytes,
        result: ParsedService,
        line: int,
        func_id: Optional[str] = None,
    ) -> None:
        """Extracts SQL keywords from string arguments in execute() / query() calls."""
        args_node = call_node.child_by_field_name("arguments")
        if not args_node:
            return
            
        for child in args_node.children:
            if child.type in ("string", "template_string", "string_content"):
                raw = child.text.decode("utf-8", errors="ignore").strip("\"'` ")
                
                if is_sql_write(raw):
                    result.edges.append(GraphEdge(
                        source_id=func_id or result.service_id,
                        target_id="__SQL_WRITE_TARGET__",
                        edge_type=EdgeType.WRITES_TO,
                        source_file=result.source_file,
                        source_line=line,
                        metadata={"pattern": "raw_sql_write", "sql_prefix": raw[:60]},
                    ))
                elif is_sql_read(raw):
                    result.edges.append(GraphEdge(
                        source_id=func_id or result.service_id,
                        target_id="__SQL_READ_TARGET__",
                        edge_type=EdgeType.READS_FROM,
                        source_file=result.source_file,
                        source_line=line,
                        metadata={"pattern": "raw_sql_read", "sql_prefix": raw[:60]},
                    ))

    # ─── Regex Fallback ───────────────────────────────────────────────────────

    def _parse_with_regex(
        self,
        source_text: str,
        language: str,
        result: ParsedService,
    ) -> None:
        """
        Lightweight regex fallback for environments without tree-sitter.
        Less precise than the tree-sitter path — used for testing only.
        """
        result.warnings.append("Using regex fallback parser (tree-sitter not available)")

        # Match function definitions to create FunctionNodes
        func_pattern = re.compile(r'^\s*(?:async\s+)?(?:def|function)\s+([a-zA-Z0-9_]+)\s*\(', re.MULTILINE)
        functions_meta = []
        for match in func_pattern.finditer(source_text):
            func_name = match.group(1)
            line = source_text[:match.start()].count('\n') + 1
            func_id = f"func_{result.service_id}_{func_name}"
            functions_meta.append({
                "name": func_name,
                "id": func_id,
                "start": match.start()
            })
            result.functions.append(FunctionNode(
                id=func_id,
                name=func_name,
                service_id=result.service_id,
                source_file=result.source_file,
                start_line=line,
            ))

        def get_func_id_for_offset(offset: int) -> Optional[str]:
            # Find the last function defined before this offset
            active_func_id = None
            for f in functions_meta:
                if f["start"] < offset:
                    active_func_id = f["id"]
                else:
                    break
            return active_func_id

        # Match .method_name( patterns
        method_pattern = re.compile(r'\.(\w+)\s*\(')
        for match in method_pattern.finditer(source_text):
            method_name = match.group(1)
            line = source_text[:match.start()].count('\n') + 1
            func_id = get_func_id_for_offset(match.start())
            self._resolve_and_add_edge(method_name, language, result, line, func_id)

        # Match raw SQL strings
        sql_patterns = [
            r'["\']+(INSERT|UPDATE|DELETE|SELECT)\s+[^"\']+["\']',
            r'`(INSERT|UPDATE|DELETE|SELECT)\s+[^`]+`',
        ]
        for pat in sql_patterns:
            for match in re.finditer(pat, source_text, re.IGNORECASE):
                raw = match.group(0).strip("\"'` ")
                line = source_text[:match.start()].count('\n') + 1
                func_id = get_func_id_for_offset(match.start())
                if is_sql_write(raw):
                    result.edges.append(GraphEdge(
                        source_id=func_id or result.service_id,
                        target_id="__SQL_WRITE_TARGET__",
                        edge_type=EdgeType.WRITES_TO,
                        source_file=result.source_file,
                        source_line=line,
                        metadata={"pattern": "raw_sql_write_regex", "sql_prefix": raw[:60]},
                    ))
                elif is_sql_read(raw):
                    result.edges.append(GraphEdge(
                        source_id=func_id or result.service_id,
                        target_id="__SQL_READ_TARGET__",
                        edge_type=EdgeType.READS_FROM,
                        source_file=result.source_file,
                        source_line=line,
                        metadata={"pattern": "raw_sql_read_regex", "sql_prefix": raw[:60]},
                    ))


# ─── Convenience function ─────────────────────────────────────────────────────

def parse_service_directory(
    directory: Path,
    service_id: str,
    service_name: Optional[str] = None,
    extensions: tuple[str, ...] = (".py", ".js", ".ts"),
) -> ParsedService:
    """
    Parse all matching source files in a directory as a single logical service.
    Edges from all files are merged into one ParsedService result.
    """
    parser = ASTParser()
    merged = ParsedService(
        service_id=service_id,
        service_name=service_name or directory.name,
        language="mixed",
        source_file=str(directory),
    )

    for ext in extensions:
        for file_path in sorted(directory.rglob(f"*{ext}")):
            lang = ASTParser._detect_language(file_path)
            parsed = parser.parse_file(file_path, service_id=service_id, service_name=service_name)
            merged.edges.extend(parsed.edges)
            merged.referenced_tables.update(parsed.referenced_tables)
            merged.warnings.extend(parsed.warnings)
            if parsed.has_lock:
                merged.has_lock = True
            if parsed.has_transaction:
                merged.has_transaction = True
            merged.language = lang  # Last file wins; good enough for prototype
            merged.local_calls.extend(parsed.local_calls)
            merged.functions.extend(parsed.functions)
            merged.imports.extend(parsed.imports)

    # Resolve local CALLS edges
    from pathlib import Path
    func_map = {(Path(f.source_file).stem, f.name): f.id for f in merged.functions}
    import_map = {}
    for src_file, mod, name in merged.imports:
        import_map.setdefault(src_file, {})[name] = mod

    for caller_id, callee_name, line, is_self, current_class, module_name in merged.local_calls:
        target_name = f"{current_class}.{callee_name}" if (is_self and current_class) else callee_name
        
        target_module = module_name
        caller_node = next((f for f in merged.functions if f.id == caller_id), None)
        if caller_node:
            if not (is_self and current_class):
                if callee_name in import_map.get(caller_node.source_file, {}):
                    target_module = import_map[caller_node.source_file][callee_name]
                    
            if (target_module, target_name) in func_map:
                merged.edges.append(GraphEdge(
                    source_id=caller_id,
                    target_id=func_map[(target_module, target_name)],
                    edge_type=EdgeType.CALLS,
                    source_file=merged.source_file,
                    source_line=line,
                    metadata={"pattern": "direct_call"}
                ))

    return merged
