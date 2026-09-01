"""
config.py
─────────────────────────────────────────────────────────────────────────────
Central configuration. All runtime values are loaded exclusively from the
environment (.env file or shell exports). Nothing is hardcoded here except
the fall-through defaults for LOCAL DEVELOPMENT ONLY (Neo4j container spun
up via docker-compose.neo4j.yml).

A forker should:
  1. Copy .env.example to .env
  2. Fill in their own values
  3. Never edit this file
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (where the forker puts it)
load_dotenv(Path(__file__).parent / '.env')

# ── Neo4j ────────────────────────────────────────────────────────────────────
# Defaults match the docker-compose.neo4j.yml shipped with this repo.
# If you change the password in docker-compose, set NEO4J_PASSWORD here too.
NEO4J_URI      = os.environ.get('NEO4J_URI',      'bolt://localhost:7687')
NEO4J_USER     = os.environ.get('NEO4J_USER',     'neo4j')
NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'omnigraph_secret_123')

# ── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')

# ── Repo → Service ID mapping ────────────────────────────────────────────────
# Set OMNIGRAPH_REPOS as a JSON string in your .env file, e.g.:
#   OMNIGRAPH_REPOS={"owner/my-python-svc": "svc_python", "owner/my-node-svc": "svc_node"}
#
# If not set, the listeners will start but have no repos to poll.
_repos_raw = os.environ.get('OMNIGRAPH_REPOS', '{}')
try:
    DEFAULT_REPOS: dict[str, str] = json.loads(_repos_raw)
except json.JSONDecodeError:
    print("[config] WARNING: OMNIGRAPH_REPOS is not valid JSON. Defaulting to empty.")
    DEFAULT_REPOS = {}
