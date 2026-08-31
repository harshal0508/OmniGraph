# OmniGraph Privacy & Data Scrubbing Policy

This document outlines the strict data sanitization policy enforced by OmniGraph's ingestion and analysis engine. 

To detect distributed race conditions, OmniGraph requires architectural context. To resolve them using the LLM Arbiter, it requires semantic context. However, **proprietary business logic, literal values, and intellectual property must never leave the local runner.**

OmniGraph resolves this tension via a **Tiered Scrubbing Policy**.

## Scrubbing Tiers

### Tier 0 — Never Leaves the Runner (Strictly Local)
The following elements are aggressively stripped from the AST before any external processing occurs. They exist only in memory during the deterministic graph-building phase and are deleted immediately after.
* **Literal values** (strings, integers, floats, booleans)
* **Comments and docstrings**
* **Full function bodies** (except for the specific structural skeleton)
* **Any code entirely unrelated to a detected datastore collision**

### Tier 1 — Structural Only (Graph Engine)
Used exclusively by the deterministic, on-runner graph builder. This data forms the mathematical topology of the application but contains no semantics.
* **Control-flow shape** (e.g., presence of `if`/`else` branches surrounding a query)
* **Edge types** (`WRITES_TO`, `READS_FROM`, `USES_LOCK`, `USES_TRANSACTION`)
* **Replica counts** (extracted from IaC manifests like `docker-compose.yml`)

### Tier 2 — Sent to LLM Arbiter (Semantic Skeleton)
When a collision is mathematically proven by the Tier 1 graph, a highly redacted "skeleton" is sent to the LLM Arbiter (Phase 3) to judge whether the collision is a true business-logic race or a harmless idempotent update.
* **Field / Table names are preserved** (e.g., `wallet_balance`, `users`). This is required to distinguish a critical financial race from a harmless `last_login_timestamp` update.
* **Surrounding code is replaced with a structural skeleton.**

#### Example Transformation
**Original Code (Tier 0 - Local):**
```python
def process_payment(req):
    # Check if user has enough funds
    user = db.query(User).filter(User.id == req.user_id).first()
    if user.wallet_balance >= req.amount:
        user.wallet_balance -= req.amount
        db.session.commit()
        return True
    return False
```

**What the LLM Arbiter Receives (Tier 2):**
```text
READ(wallet_balance) → IF <condition> → WRITE(wallet_balance)
Actors: svc_payments (replicas=3), svc_ledger (replicas=2)
Lock present: none
```

## Opt-Out & Air-Gapped Fallback
Some organizations operate under strict compliance regimes (e.g., HIPAA, SOC2, PCI-DSS) that prohibit sending even database schema names (Tier 2) off-network.

OmniGraph supports a strict **Tier 2 Opt-Out flag** (`--no-llm`). 
When enabled, the LLM Arbiter is completely bypassed. OmniGraph falls back to a deterministic **Keyword Heuristic Engine** running locally to determine severity:
* `balance | payment | credit | inventory | ledger` → **High Severity**
* `timestamp | log | view_count | last_seen | cache` → **Low Severity**
