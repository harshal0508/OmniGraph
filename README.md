# OmniGraph: Distributed Concurrency Engine

OmniGraph is a static analysis engine designed to catch distributed concurrency bugs — specifically, unsynchronized database writes across independent microservices that share a data store.

It intercepts pull requests, parses abstract syntax trees across an entire organization's repositories into a global graph, and fails PRs mathematically before deadlocks or data corruption reach production.

## 💥 The Problem it Solves

In large-scale distributed architectures, a single logical table (e.g. `users`) is often read from and written to by dozens of disconnected microservices. 

If Developer A (working in the `auth` repo) writes to `users` using a Postgres advisory lock, and Developer B (working in the `billing` repo) pushes code that writes to `users` using a Redis lock (or no lock at all), traditional CI/CD tools will pass both PRs. The tests will greenlight because both services are functionally correct in isolation.

In production, these two operations will hit the database at the exact same millisecond, ignoring each other's locks, and silently corrupt the data. 

**This is not theoretical.** In a highly-publicized incident, AWS DynamoDB experienced a catastrophic 20-hour outage across the US-East-1 region because two independent microservices attempted to update a central routing table using mismatched synchronization assumptions. Traditional integration tests structurally cannot catch this category of cross-repo Time-of-Check-to-Time-of-Use (TOCTOU) race conditions.

OmniGraph solves this by mapping data ownership across the organization. *(Note: While the DynamoDB incident is the exact class of bug OmniGraph is built for, OmniGraph's efficacy on systems of that scale is currently extrapolated from tests on smaller live repositories, not yet directly proven on Amazon-scale infrastructure).*

## ⚙️ How It Works

OmniGraph operates entirely independently of your active codebase via an asynchronous webhook pipeline.

1. **AST Parsing (Semantic Extraction):** 
   When a developer opens a PR, OmniGraph pulls the file and parses it using language-native Abstract Syntax Trees (not brittle Regex strings). It detects ORM calls (e.g., `Django.transaction.atomic()` or `Sequelize.findAll()`) and converts them into semantic edges: `WRITES_TO`, `READS_FROM`, `USES_LOCK`, `USES_TRANSACTION`.

2. **The Global Graph (Neo4j):**
   The extracted edges are merged into a Neo4j graph database containing the AST representation of every other repository in the organization. The PR code is never merged into the graph during the check; instead, it is passed as parameters to a read-only Cypher query that asks: *"Does this proposed write collide with any existing function in any other service?"*

3. **Mechanism Symmetry Resolution:**
   OmniGraph doesn't just check if a lock is present — it extracts the specific distributed lock mechanism (e.g., `redis_lock`, `pg_advisory_lock`, `sync.Mutex`). If the PR adds a Redis lock, but the colliding microservice uses a Postgres lock, OmniGraph will flag the PR as unsafe because the locking mechanisms do not coordinate.

## ⚠️ Known Limitations & Boundaries

OmniGraph is highly accurate at a structural level, but operates with several explicit bounds:

- **Row-Level Precision:** The engine maps data flows at the `Table` level. If Service A writes to User 123 and Service B writes to User 456, OmniGraph will flag it as a concurrency risk. Distinguishing non-overlapping row-level access requires dynamic runtime tracing (e.g. eBPF) which is explicitly outside the scope of static AST analysis.
- **Lock-Key Matching:** While OmniGraph verifies that both services use the same lock *mechanism* (e.g. both use Redis), it does not yet evaluate the precise AST arguments to guarantee their lock *keys* match (e.g., `lock(user_id)` vs `lock(order_id)`). 
- **Raw SQL Regex Fragility:** The AST parser natively resolves standard ORM boundaries. However, for raw SQL strings (`db.execute('UPDATE users...')`), it relies on a lightweight heuristic (`(?i)\b(?:FROM|UPDATE|INTO|JOIN)\s+([a-zA-Z0-9_]+)`). This regex is fragile and will silently fail on quoted identifiers (e.g. `` UPDATE `users` ``) or schema-prefixed tables (`UPDATE public.users`). Robust AST-level semantic parsing of raw SQL is deferred to v2.

## 🧾 Proof of Work

The core engine is not a local simulation. The PR ingestion pipeline, Neo4j resolution logic, and automated GitHub feedback cycle are running live against real infrastructure.

**Live PR Interception:** [tzegoat9/python-mini-projects/pull/6](https://github.com/tzegoat9/python-mini-projects/pull/6)
*In this PR, an asynchronous background listener automatically detected a simulated external push, fetched the PR file via the GitHub API, dynamically extracted the `db.execute('UPDATE users...')` AST write, queried a Neo4j container holding a separate Node.js repository, discovered the cross-repo collision, and successfully blocked the PR by posting a formatted concurrency warning.*
