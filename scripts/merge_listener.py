import os
import sys
import time
import shutil
from pathlib import Path
from github import Github

from scripts.merge_pr import merge_pr
from config import GITHUB_TOKEN, DEFAULT_REPOS

SOURCE_EXTENSIONS = {'.py', '.js', '.ts', '.yml', '.yaml'}

def fetch_pr_sources_to_disk(repo, pr, dest_dir: Path) -> int:
    changed = list(pr.get_files())
    written = 0
    for f in changed:
        suffix = Path(f.filename).suffix
        if suffix not in SOURCE_EXTENSIONS:
            continue
        if f.status == 'removed':
            continue
        try:
            content_obj = repo.get_contents(f.filename, ref=pr.merge_commit_sha)
            dest_path = dest_dir / f.filename
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content_obj.decoded_content)
            written += 1
        except Exception as e:
            print(f"  [WARN] Could not fetch {f.filename}: {e}")
            
    # ALWAYS try to fetch .omnigraph.yml for context
    try:
        yml_obj = repo.get_contents(".omnigraph.yml", ref=pr.merge_commit_sha)
        dest_path = dest_dir / ".omnigraph.yml"
        dest_path.write_bytes(yml_obj.decoded_content)
    except:
        pass
        
    return written

def run_merge_listener():
    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN not set in config.")
        sys.exit(1)
        
    g = Github(GITHUB_TOKEN)
    
    print("Initializing OmniGraph Multi-Repo Merge Listener...")
    repo_objs = {}
    seen_merged_prs = {}

    for repo_name, svc_id in DEFAULT_REPOS.items():
        repo = g.get_repo(repo_name)
        repo_objs[repo_name] = repo
        seen = set()
        for count, pr in enumerate(repo.get_pulls(state='closed', sort='updated', direction='desc')):
            if count >= 10:
                break
            if pr.merged:
                seen.add(pr.number)
        seen_merged_prs[repo_name] = seen
        print(f"  [{repo_name}] tracking {len(seen)} past merged PRs. (Service ID: {svc_id})")

    print("Starting polling loop for newly MERGED pull requests across all repos...")

    try:
        while True:
            time.sleep(5)
            for repo_name, repo in repo_objs.items():
                svc_id = DEFAULT_REPOS[repo_name]
                recent_closed = []
                for count, pr in enumerate(repo.get_pulls(state='closed', sort='updated', direction='desc')):
                    if count >= 5:
                        break
                    recent_closed.append(pr)

                for pr in recent_closed:
                    if pr.merged and pr.number not in seen_merged_prs[repo_name]:
                        seen_merged_prs[repo_name].add(pr.number)
                        print(f"\n>>> [MERGE DETECTED] {repo_name} PR #{pr.number} merged into {repo.default_branch}! Triggering OmniGraph ingestion...")

                        dest_dir = Path(f"tmp_repo_{repo_name.replace('/', '_')}_{pr.number}")
                        if dest_dir.exists():
                            shutil.rmtree(dest_dir, ignore_errors=True)
                        dest_dir.mkdir(parents=True)

                        try:
                            n_files = fetch_pr_sources_to_disk(repo, pr, dest_dir)
                        except Exception as e:
                            print(f"[ERROR] Failed to fetch files for PR #{pr.number}: {e}")
                            shutil.rmtree(dest_dir, ignore_errors=True)
                            continue

                        if n_files == 0:
                            print(f"[INFO] PR #{pr.number} has no source changes.")
                            shutil.rmtree(dest_dir, ignore_errors=True)
                            continue

                        try:
                            merge_pr(str(dest_dir), svc_id, dry_run=False)
                            print(f"[OK] Successfully ingested {repo_name} PR #{pr.number} into Neo4j.")
                        except SystemExit:
                            print(f"[ERROR] Ingestion failed (SystemExit).")
                        except Exception as e:
                            print(f"[ERROR] Ingestion failed for PR #{pr.number}: {e}")
                        finally:
                            if dest_dir.exists():
                                shutil.rmtree(dest_dir, ignore_errors=True)

    except KeyboardInterrupt:
        print("Shutting down merge listener.")

if __name__ == '__main__':
    run_merge_listener()
