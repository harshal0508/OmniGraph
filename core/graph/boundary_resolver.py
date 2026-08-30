"""
core/graph/boundary_resolver.py
─────────────────────────────────────────────────────────────────────────────
Boundary Node Resolver (OmniGraph v2)

Normalizes framework-specific table names, model class names, and ORM
references into canonical database table IDs. 

Example:
    "Order" (Django model) -> "table_order"
    "public.orders" (Raw SQL / Knex) -> "table_orders"
    "Orders" (Prisma) -> "table_orders"
"""

import re
import inflect

_p = inflect.engine()

def normalize_table_name(raw_name: str) -> str:
    """
    Converts a raw string reference into a canonical table ID.
    - Strips schema prefixes (e.g., 'public.')
    - Converts PascalCase (Models) to snake_case
    - Lowercases everything
    - Converts plurals to singular using robust dictionary-backed inflection (inflect)
    """
    if not raw_name or raw_name.startswith("__UNRESOLVED"):
        return raw_name

    # 1. Strip schema prefixes (public.users -> users)
    if "." in raw_name:
        raw_name = raw_name.split(".")[-1]
    
    # 2. Strip quotes
    raw_name = raw_name.replace('"', '').replace("'", "").replace("`", "")

    # 3. Convert PascalCase to snake_case (e.g. OrderHistory -> order_history)
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', raw_name)
    snake_cased = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    # 4. Real plural stripping using inflect
    # If the word is plural, _p.singular_noun returns the singular string.
    # If the word is already singular (e.g. 'status', 'address'), it returns False.
    singular = _p.singular_noun(snake_cased)
    
    # inflect has a known bug where it stems 'address' -> 'addres'
    if singular and snake_cased.endswith('ss') and singular == snake_cased[:-1]:
        canonical = snake_cased
    else:
        canonical = singular if singular else snake_cased

    # 5. Fallback cleanup (no special chars)
    canonical = re.sub(r'[^a-z0-9_]', '', canonical)

    return f"table_{canonical}"

def resolve_boundary_node(target_hint: str, fallback_db_id: str) -> str:
    """
    Resolves the canonical node ID for a database table.
    """
    if target_hint and target_hint not in ("__UNRESOLVED_TABLE__", "__SQL_WRITE_TARGET__", "__SQL_READ_TARGET__"):
        return normalize_table_name(target_hint)
    
    # If no hint provided, fallback to the database's default placeholder
    return f"table_{fallback_db_id}_default"
