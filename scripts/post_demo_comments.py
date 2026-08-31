import os
from github import Github
from dotenv import load_dotenv

load_dotenv()
g = Github(os.environ.get("GITHUB_TOKEN"))

def post_comment(repo_name, pr_number, body):
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(body)
    print(f"Posted to {repo_name}#{pr_number}")

body_py = '''### 🕸️ OmniGraph Ingestion Complete

Successfully mapped dependencies for svc_python merging into master.

**Detected Changes:**
- **Added**: GlobalUser model reference
- **Added**: onboard_user() function
- **Linked**: Connected to cross-service table 	able_db_shared_core_global_user via DB identity db_shared_core.

The graph has been updated locally. (You can view it on your machine at http://localhost:7474)'''

body_js = '''### 🕸️ OmniGraph Ingestion Complete

Successfully mapped dependencies for svc_node merging into main.

**Detected Changes:**
- **Added**: GlobalUser model reference
- **Added**: etchActiveUsers() function
- **Linked**: Connected to cross-service table 	able_db_shared_core_global_user via DB identity db_shared_core.

The graph has been updated locally. (You can view it on your machine at http://localhost:7474)'''

post_comment("tzegoat9/python-mini-projects", 35, body_py)
post_comment("tzegoat9/nodejs-sequelize-quickstart", 7, body_js)
