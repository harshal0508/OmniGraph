# OmniGraph

**A persistent, cross-repository graph that catches unsynchronized database writes across independent services — before they reach production.**

OmniGraph parses your organization's repositories at the AST level, builds a shared graph of which service writes to (or reads from) which table, and flags PRs that introduce a write with no coordination against something another service already does — even if that other service lives in a completely different repo, language, or team.

---

## The Problem

Modern engineering orgs split work across many independently-owned repositories. It's extremely common — and often architecturally reasonable — for more than one of these services to read or write the same underlying database table, sometimes through completely different ORMs, sometimes via raw SQL.

When two services write to the same data **without coordinating** (no shared lock, no shared transaction), you get a distributed race condition: two operations interleave badly, and one silently overwrites or corrupts the other's write. Each service passes its own tests. Each service is internally correct. The bug only appears under real concurrent production traffic, and by the time anyone notices, the damage is already done.

**This isn't theoretical.** In October 2025, two internal AWS automation processes — each individually correct — raced to update the same DNS state with no atomic coordination between them. One silently deleted the other's valid record. The result was a ~15-hour outage that took down a meaningful share of the internet. That incident was a same-team, tightly-coupled race, not the cross-team scenario OmniGraph is built for — but it's the clearest public proof that this exact failure mechanism (uncoordinated concurrent writes to shared state) is real and can be catastrophic, at any scale, even inside the most sophisticated engineering orgs on Earth.

## Why Existing Tools Don't Catch This

| Tool category | What it actually sees | Why it misses this |
|---|---|---|
| CI/CD pipelines | One repo, one PR, in isolation | No memory of what other repos do |
| AI code reviewers (Copilot, CodeRabbit) | One diff at a time | No persistent state across repos or sessions, by their own documentation |
| AWS CodeGuru Reviewer | Single-repo, single-language (Java/Python) | Thread-level concurrency within one program, not cross-service DB writes |
| Data lineage tools (DataHub, OpenLineage) | Warehouse/pipeline layer (Snowflake, dbt, Airflow) | Built for analytics data flow, not source-level OLTP writes; no concept of lock coordination |
| Kafka / message queues | Event ordering, if configured correctly | Doesn't help when two *different* services independently write to the same table through unrelated code paths |
| "Just use database-per-service" | The real architectural fix | Requires committing to a migration; many real orgs share a database deliberately or by legacy accident, and OmniGraph exists for that population |

The gap isn't that the industry doesn't know how to *prevent* races — locks and transactions exist. The gap is that nobody maintains a **persistent, cross-repo, cross-language map** of who touches what, so a developer can be warned *before* merge instead of finding out in an incident postmortem.

## How It Works

1. **Bulk ingestion** — every connected repo is parsed (tree-sitter, Python/JS/TS) into functions and their database read/write edges, and merged into a shared Neo4j graph. Table identity is normalized across languages and ORM conventions (casing, pluralization, explicit `__tablename__`/`db_table`/`tableName` declarations) so `InventoryItem`, `inventory_items`, and `INVENTORY_ITEMS` all resolve to one canonical node.
2. **PR-time check** — when a developer opens a PR, a fast, read-only query checks the change against the *already-persisted* graph for other services' writes to the same table, with no write locks and no re-scan of the whole org.
3. **Mechanism-symmetry resolution** — a shared table with locks on both sides isn't automatically safe. OmniGraph checks whether both sides use the *same* coordination mechanism (a Redis lock and a Postgres advisory lock never actually coordinate with each other, even though both "have a lock").
4. **Arbiter triage** — for structurally ambiguous collisions, a single LLM call reasons over a privacy-scrubbed structural skeleton (never raw source, never variable values) to judge severity, defaulting to an honest "uncertain" verdict rather than guessing when evidence is thin.
5. **Re-scan verification** — after a fix is pushed, the collision is re-checked on **both sides**, so a one-sided fix (you added a lock, the other service still doesn't check it) is correctly reported as partially resolved, not silently cleared.
6. **Merge-triggered persistence** — on merge, the graph is atomically updated to reflect the new state, including correctly removing edges for deleted code, so it never goes stale.

## What OmniGraph Is Not

- **Not a replacement for `database-per-service`.** If your org has already made that migration, cross-repo collisions are structurally impossible and this tool has nothing to catch. OmniGraph exists for the (very common) population that hasn't made that move, or can't for legacy/organizational reasons.
- **Not a runtime enforcement tool.** It never blocks a merge — findings are posted as PR comments. Trust has to be earned with a low false-positive rate before any gate becomes automatic.
- **Not a data-at-rest or PII scanner.** That's a different, well-served market (DSPM tools) with different infrastructure requirements (cloud credentials, not source access).

## Known Limitations

Stated plainly, not hidden in fine print:

- **Table-level, not row-level.** Service A writing `user_id=123` and Service B writing `user_id=456` both flag as risk, even though they don't actually collide. Row-level precision would require dynamic tracing (e.g. eBPF), out of scope for static analysis.
- **Lock-key matching is unverified.** OmniGraph checks that both sides use the same lock *mechanism* (e.g. both Redis), but not that they lock on the same *key* (`lock(user_id)` vs `lock(order_id)`).
- **Raw SQL extraction is a bounded heuristic**, not real parsing — it will fail on quoted identifiers, schema prefixes, and parameterized queries. Explicit ORM declarations and argument-based extraction are handled deterministically and don't have this weakness.
- **Deterministic table-name resolution has a measured ceiling** (roughly 60-90% depending on code style), honestly measured against real fixtures rather than assumed. Ambiguous edges are dropped and logged, never silently guessed.
- **Domain aliasing with zero code-level connection is unsolved by design.** If two teams call the same physical table by two unrelated words (`Users` vs `Payees`) decided in a meeting and never written in either codebase, no static analysis can discover it. A manual override (`.omnigraph.yml`) exists for exactly this case.
- **No verified real-world cross-team incident yet.** The AWS example is strong evidence for the underlying mechanism class; it is a same-team, multi-replica race, not the cross-team scenario this tool specifically targets. That remains the most valuable open validation gap.
- **Requires broad repo access.** Connecting an org's full repo set to a third-party graph is an organizational and security decision before it's a technical one.

## Architecture

```
core/ingestion/           - AST parsing, ORM semantic mapping, IaC (Docker/K8s) parsing
core/graph/               - Neo4j graph builder, table/DB identity resolution, boundary normalization
core/arbiter/             - LLM-based severity triage (structural skeleton only, never raw code)
core/eval/                - benchmark suite (structural + adversarial + real-world OSS cases)
config.py                 - all repo mappings, credentials, and paths - single source of config
scripts/merge_pr.py       - persistence hook: resolves and atomically commits merged state to the graph
scripts/check_listener.py - PR-check polling daemon (read-only)
scripts/merge_listener.py - merge-trigger polling daemon (write path)
```

## Deployment

OmniGraph has two distinct deployment modes with different capabilities. Understanding the boundary before you install either is important.

---

### Option A — GitHub Action (single-repo detection)

**What it does:** Scans the repository it runs in. Detects TOCTOU loops, missing distributed locks, non-idempotent retries, and Redis non-atomic read-modify-write patterns within that single codebase. Posts findings as a PR comment. No external infrastructure required.

**What it does not do:** Cross-repo detection — it has no visibility into what other services in your organization write to the same tables. If cross-repo collision detection is what you need, use Option B.

Add to your workflow:

```yaml
- name: OmniGraph Architecture Scan
  uses: harshal0508/OmniGraph@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    fail_on_critical: 'false'   # recommended: comment-only mode first, opt into blocking later
```

Optionally enable AI severity enrichment (advisory text only — no code generation):

```yaml
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    # or
    google_api_key: ${{ secrets.GOOGLE_API_KEY }}
```

---

### Option B — Self-hosted full engine (cross-repo detection)

**What it does:** Everything in Option A, plus persistent cross-repo graph matching. When a PR touches a table that another service in a completely different repository already writes to, OmniGraph flags the collision — even across different languages, ORMs, and teams.

**What it requires:** A persistent Neo4j instance reachable from wherever the listeners run (local server, VM, managed Neo4j Aura, etc.). This is an organizational infrastructure decision, not just a credential swap.

**Prerequisites:** Docker, Python 3.10+, a GitHub PAT with Contents: read and Pull requests: read/write on all repos you want connected.

```bash
git clone https://github.com/harshal0508/OmniGraph.git
cd OmniGraph
pip install -r requirements.txt

cp .env.example .env
# Edit .env:
#   GITHUB_TOKEN   — your GitHub PAT
#   OMNIGRAPH_REPOS — JSON map: {"owner/repo": "service_id", ...}
#   NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD — your Neo4j instance

docker-compose -f docker-compose.neo4j.yml up -d   # or point at an existing instance
```

**Run the components:**

```bash
python -m core.eval.benchmark                        # verify the engine works (28 cases, no Neo4j needed)
python scripts/merge_pr.py <repo-path> <service-id>  # bulk-ingest a repo into the graph
python scripts/check_listener.py                     # PR-check daemon (read-only, polls configured repos)
python scripts/merge_listener.py                     # merge-trigger daemon (updates graph on merge)
```

All configuration is read from `.env` via `config.py` — no repo names, service IDs, or credentials are hardcoded in source. See `.env.example` for every required variable and its expected format.

## Proof of Work

This isn't a local simulation. The full pipeline — AST extraction, graph resolution, atomic persistence, and GitHub PR interception — has been run live against real GitHub infrastructure, with results independently verified via raw Cypher queries and direct assertion checks rather than trusted from script output alone. See `omnigraph_deep_dive.md` for the full verification history, including deliberate stress tests designed to break the system (adversarial naming, lock-mechanism mismatches, concurrent write races, network failure handling, cold-start config validation) before trusting it.

## License

Open source. Built as a deep-dive into distributed systems, static analysis, and graph-based architecture — released for platform engineering teams who share this exact problem and can't justify a six-figure ASPM contract to solve one specific hazard class.
