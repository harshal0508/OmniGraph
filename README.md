# OmniGraph

A lightweight, cross-repo structural code graph for detecting distributed concurrency risks before they merge.

OmniGraph parses Pull Requests for ORM database interactions, pushes the extracted relationships into a central Neo4j graph, and blocks PRs when it detects that two separate repositories touch the same database table without a recognized shared locking mechanism.

---

## What it actually is

OmniGraph is a **heuristic-based architectural mapping tool**, not a deep data-flow analyzer.

- It recognizes standard ORM patterns (Django, SQLAlchemy, Sequelize, Mongoose) and extracts the database tables they interact with.
- It stores those relationships (`Service → Function → Table`) in a persistent Neo4j graph that survives individual CI runs.
- When a new PR arrives, it queries the graph to find if any *other* service in any *other* repository is already writing to the same tables.

**What it does not do:** row-level precision, full data-flow tracking, or formal verification. See [Known Limitations](#known-limitations).

---

## Quickstart

### 1. Prerequisites

- Python 3.10+
- Docker (for Neo4j)

### 2. Clone and install dependencies

```bash
git clone https://github.com/harshal0508/OmniGraph.git
cd OmniGraph
pip install -r requirements.txt
```

### 3. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | What to set |
|---|---|
| `GITHUB_TOKEN` | A GitHub PAT with `repo` read and `write:discussion` scopes |
| `OMNIGRAPH_REPOS` | A JSON map of `{"owner/repo": "service_id"}` for every repo you want to monitor |
| `NEO4J_PASSWORD` | Leave as-is if using the bundled docker-compose; change if using your own instance |

### 4. Start Neo4j

```bash
docker compose -f docker-compose.neo4j.yml up -d
```

Neo4j will be available at `http://localhost:7474` (browser) and `bolt://localhost:7687` (driver).  
Default credentials: `neo4j` / `omnigraph_secret_123` (set in `docker-compose.neo4j.yml` and `.env`).

### 5. Run the benchmark to verify the engine works

```bash
python main.py benchmark
```

This runs the self-contained fixture suite against your local setup. No GitHub token or external repos needed.

---

## Usage

All commands go through `main.py`:

```bash
# Run the static analysis engine on a local directory
python main.py scan path/to/your/service/

# Bulk ingest a local repository into the Neo4j graph
python main.py ingest path/to/your/service/ your_service_id

# Start polling configured repos for new PRs and checking them
python main.py check-listener

# Start polling for merged PRs and updating the graph
python main.py merge-listener

# Run the benchmark suite
python main.py benchmark
```

---

## How the PR check works

1. `check-listener` polls every repo in `OMNIGRAPH_REPOS` every 10 seconds.
2. When a new PR is opened, it fetches the changed `.py`, `.js`, and `.ts` files via the GitHub API.
3. It parses each file's AST for ORM interactions.
4. It queries the Neo4j graph: *"Does any other service already write to these tables?"*
5. If a collision is found without matching locks, it posts a warning comment directly to the PR.

---

## Known Limitations

These are real, documented constraints — not hedges.

- **Table-level granularity, not row-level.** If Service A updates `users` row 1 and Service B updates `users` row 2, OmniGraph will still flag it as a potential collision. It cannot distinguish non-overlapping row access without runtime tracing.
- **ORM patterns only.** The AST parser recognizes specific ORM signatures. Raw SQL strings (`db.execute("UPDATE users ...")`) fall back to a lightweight regex heuristic that fails on quoted identifiers and schema-prefixed tables (e.g., `UPDATE public.users`).
- **Lock-key matching is not implemented.** OmniGraph checks that both services use the *same lock mechanism* (e.g., both use Redis), but does not parse the lock key arguments to verify they actually lock the same resource.
- **False positives at table level.** This is the highest-noise limitation. Shared tables with non-overlapping access patterns will be flagged. Treat OmniGraph as a smoke alarm: it tells you to look, not that the house is definitely on fire.

---

## Architecture

```
.
├── core/
│   ├── ingestion/
│   │   ├── ast_parser.py       # Multi-language ORM pattern extractor
│   │   └── iac_parser.py       # IaC manifest parser (Docker Compose, K8s)
│   ├── graph/
│   │   ├── neo4j_builder.py    # Persistent graph writer (MERGE-idempotent)
│   │   ├── target_resolver.py  # Canonicalizes table IDs across repos
│   │   └── rules/              # Detection rules (race_condition, toctou, etc.)
│   ├── arbiter/                # Optional LLM enrichment layer
│   └── reporter/               # CLI and GitHub PR report formatters
├── scripts/
│   ├── check_listener.py       # PR-check polling daemon
│   ├── merge_listener.py       # Post-merge graph update daemon
│   └── merge_pr.py             # Bulk local repo ingestion
├── tests/
│   └── eval_dataset/           # Benchmark fixtures (self-contained)
├── config.py                   # Single source of truth for all env vars
├── main.py                     # Unified CLI entry point
├── docker-compose.neo4j.yml    # Neo4j container definition
└── .env.example                # Template — copy to .env and fill in
```

---

## Proof of concept

The cross-repo graph linkage, Neo4j persistence, and automated PR comment were tested live against two real repositories:

- [tzegoat9/python-mini-projects #35](https://github.com/tzegoat9/python-mini-projects/pull/35) — Python service (`svc_python`)
- [tzegoat9/nodejs-sequelize-quickstart #7](https://github.com/tzegoat9/nodejs-sequelize-quickstart/pull/7) — Node.js service (`svc_node`)

Both services were configured to share the same canonical database identity via `.omnigraph.yml`. The listener detected the cross-repo table overlap and posted formatted collision warnings to both PRs automatically.

---

## Contributing

This is an open-source tool for Platform Engineering teams. Issues and PRs are welcome.

If OmniGraph silently misses a database interaction in your codebase, open an issue with the ORM pattern and we will add it to the parser.
