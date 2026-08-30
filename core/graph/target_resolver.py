from core.graph.boundary_resolver import normalize_table_name
import re

def resolve_target(target_id: str, metadata: dict) -> str:
    """
    Unified Target Resolver (V1 Heuristic Fallback).
    Takes an unresolved edge target and metadata from the AST,
    and returns a canonical table name.
    
    If the LLM Arbiter were active in the webhook loop, it would
    intercept these tokens and resolve them using cross-repo context.
    """
    if target_id not in ("__UNRESOLVED_TABLE__", "__SQL_WRITE_TARGET__", "__SQL_READ_TARGET__"):
        return target_id
        
    if not metadata:
        return target_id
        
    hint = None
    
    # 1. ORM Extraction (e.g. Settings.update() -> Settings)
    if "orm_model" in metadata:
        hint = metadata["orm_model"]
        
    # 2. Raw SQL Regex (e.g. UPDATE users -> users)
    elif "sql_prefix" in metadata:
        m = re.search(r'(?i)\b(?:FROM|UPDATE|INTO|JOIN)\s+([a-zA-Z0-9_]+)', metadata["sql_prefix"])
        if m:
            hint = m.group(1).lower()
            
    if hint:
        # V1 Fallback: we just return the normalized base string.
        # V2 Arbiter would prepend the correct db_id (e.g. table_db_prod_users).
        # We strip the "table_" prefix from boundary_resolver to match the V1 string conventions 
        # used in the listener and raw string matching.
        norm = normalize_table_name(hint)
        if norm.startswith("table_"):
            return norm[6:]
        return norm
        
    return target_id
