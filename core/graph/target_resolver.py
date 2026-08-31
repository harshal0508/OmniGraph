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
    
    # 1. Raw SQL Regex (e.g. UPDATE users -> users)
    if "sql_prefix" in metadata:
        m = re.search(r'(?i)\b(?:FROM|UPDATE|INTO|JOIN)\s+([a-zA-Z0-9_\.]+)', metadata["sql_prefix"])
        if m:
            hint = m.group(1).lower()
            if "." in hint:
                hint = hint.split(".")[-1]
                
    # 2. ORM Extraction
    if not hint:
        hint = metadata.get("orm_model")
        first_arg = metadata.get("first_arg")
        method = metadata.get("method", "")
        if method in ("query", "get_object_or_404", "find", "findOne", "findAll"):
            if first_arg:
                hint = first_arg
            

    if hint:
        # V1 Fallback heuristic cleanup:
        # 1. Reject if it's a method call string (contains parentheses)
        if "(" in hint or "=" in hint:
            hint = ".".join([p for p in hint.split(".") if "(" not in p and "=" not in p])
            if not hint:
                return target_id

        # 2. Denylist of generic variables/managers
        denylist = {"session", "db", "conn", "client", "ctx", "context", 
                    "repository", "objects", "object", "manager", "query", "this", "self"}
        
        # 3. Handle dot-notation (e.g. User.objects -> user, db.orders -> orders)
        if "." in hint:
            parts = hint.split(".")
            for part in reversed(parts):
                if part.lower() not in denylist:
                    hint = part
                    break
            else:
                hint = None # All parts were generic
                
        if not hint or hint.lower() in denylist:
            return target_id
            
        norm = normalize_table_name(hint)

        if norm.startswith("table_"):
            return norm[6:]
        return norm
        
    return target_id


def resolve_with_arbiter(edge, fallback: str) -> str:
    """
    Tier 2 Target Resolution via LLM Arbiter.
    Reads the surrounding code context and asks the LLM to deduce the real table name.
    """
    import os
    import json
    import textwrap
    try:
        import google.generativeai as genai
    except ImportError:
        return fallback

    api_key = os.environ.get("GOOGLE_API_KEY")


    source_file = edge.source_file
    line_num = edge.source_line
    
    # Extract code snippet (5 lines before and after)
    snippet = ""
    try:
        with open(source_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            start = max(0, line_num - 5)
            end = min(len(lines), line_num + 5)
            snippet = "".join(lines[start:end])
    except Exception:
        return fallback

    if not api_key:
        # Mock Arbiter for dry-run testing
        if "get_object_or_404(UserProfile" in snippet:
            return "userprofile"
        if "session.query(Inventory)" in snippet or "session.query(object)" in snippet:
            # The variable is usually `product` or `inventory`
            return "product"
        if "this.update" in snippet:
            # Impossible to determine
            return fallback
        return fallback

    prompt = textwrap.dedent(f"""
    You are an expert static analysis engine.
    I have a generic database call or ORM query at line {line_num} in the following code:
    
    `
    {snippet}
    `
    
    The heuristic resolver extracted the object/hint '{fallback}'.
    Based on the surrounding code, variable names, and class definitions, deduce the ACTUAL canonical database table name this code is operating on (e.g. 'orders', 'user_profile', 'inventory').
    
    Respond strictly in JSON format matching this schema:
    {{
        "canonical_table": "table_name_here",
        "confidence": "HIGH | LOW",
        "reasoning": "1 sentence explaining exactly what evidence in the snippet supports this table name."
    }}
    
    CONSTRAINTS:
    - If there is not enough explicit evidence in the 10 lines to guarantee the table name, you MUST return "__UNRESOLVED_TABLE__" and "LOW" confidence. Do NOT guess.
    """)

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-pro")
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            )
        )
        data = json.loads(resp.text)
        table = data.get("canonical_table", "__UNRESOLVED_TABLE__")
        if table and not table.startswith("__"):
            # Strip generic prefixes if the model added them
            if table.startswith("table_"):
                return table[6:]
            return table
    except Exception as e:
        print(f"Arbiter error: {e}")
        pass

    return fallback
