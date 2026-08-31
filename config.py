import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(os.path.expanduser('~/.env')))
load_dotenv(Path('.env'))

NEO4J_URI = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.environ.get('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.environ.get('NEO4J_PASSWORD', 'omnigraph_secret_123')

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')

# Default repos for the listeners to poll
DEFAULT_REPOS = {
    "tzegoat9/python-mini-projects": "svc_python",
    "tzegoat9/nodejs-sequelize-quickstart": "svc_node"
}
