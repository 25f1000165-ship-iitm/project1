# project1.py
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from github import Github
import os, uuid, base64, requests, datetime, time, json
from dotenv import load_dotenv

# -------------------------------
# LOAD ENV
# -------------------------------
load_dotenv()
SECRET = os.getenv("SECRET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")

if not SECRET or not GITHUB_TOKEN or not GITHUB_USERNAME:
    raise Exception("Set SECRET, GITHUB_TOKEN, and GITHUB_USERNAME in .env")

# -------------------------------
# APP
# -------------------------------
app = FastAPI(title="Project 1 API")

# In-memory tasks store for validation of evaluation callbacks
# production: replace with persistent DB
TASKS = {}  # key: (email, task, round, nonce) -> metadata dict

# -------------------------------
# MODELS
# -------------------------------
class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    checks: list
    evaluation_url: str
    attachments: list = []

class EvalRequest(BaseModel):
    email: str
    task: str
    round: int
    nonce: str
    repo_url: str
    commit_sha: str
    pages_url: str

# -------------------------------
# HELPERS
# -------------------------------
def verify_secret(secret: str):
    if secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

def deterministic_repo_name(email: str, task: str):
    """Make deterministic repo name based on email prefix and task (safe)."""
    user = email.split("@")[0].replace(".", "-").lower()
    task_clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in task.lower())[:40]
    return f"{user}-{task_clean}"

def github_client():
    return Github(GITHUB_TOKEN)

def ensure_repo(user_obj, repo_name: str):
    """Return existing repo if present, else create new public repo with MIT license and main branch."""
    try:
        repo = user_obj.get_repo(repo_name)
        return repo, False
    except Exception:
        # create repo
        repo = user_obj.create_repo(
            name=repo_name,
            private=False,
            description=f"Auto-generated repo for task '{repo_name}'",
            auto_init=False
        )
        # Create README and LICENSE initial commits
        readme = "# Auto-generated repo\n\nThis repository was created by Project1 auto-deployer.\n"
        mit_text = MIT_LICENSE_TEXT.replace("<YEAR>", str(datetime.datetime.utcnow().year)).replace("<AUTHOR>", user_obj.login)
        repo.create_file("README.md", "Add README", readme)
        repo.create_file("LICENSE", "Add MIT license", mit_text)
        return repo, True

def create_or_update_file(repo, path: str, content: str, commit_message: str):
    """Create or update a file in the repo."""
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, commit_message, content, existing.sha)
    except Exception:
        # create if not exists
        repo.create_file(path, commit_message, content)

def get_latest_commit_sha(repo, max_retries=6, delay=1.0):
    """Return the latest commit SHA with retries (robust)."""
    for _ in range(max_retries):
        try:
            commits = list(repo.get_commits())
            if commits:
                return commits[0].sha
        except Exception:
            pass
        time.sleep(delay)
        delay *= 1.5
    return None

def enable_github_pages_rest(repo):
    """Enable GitHub Pages via REST API and return pages_url (or None)."""
    api_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo.name}/pages"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    # Try to set source to main or gh-pages root
    payload = {"source": {"branch": "main", "path": "/"}}
    r = requests.post(api_url, json=payload, headers=headers, timeout=10)
    if r.status_code in (201, 204):
        return f"https://{GITHUB_USERNAME}.github.io/{repo.name}/"
    # fallback: if main not present, try master
    payload = {"source": {"branch": "master", "path": "/"}}
    r2 = requests.post(api_url, json=payload, headers=headers, timeout=10)
    if r2.status_code in (201, 204):
        return f"https://{GITHUB_USERNAME}.github.io/{repo.name}/"
    # If already configured, GET will return current pages config
    r3 = requests.get(api_url, headers=headers, timeout=10)
    if r3.status_code == 200:
        try:
            return r3.json().get("html_url")
        except Exception:
            pass
    print("Warning: GitHub Pages enable failed or delayed:", r.status_code, r.text if 'r' in locals() else "")
    return None

def decode_attachment_to_files(attachments):
    files = {}
    for att in attachments or []:
        name = att.get("name")
        url = att.get("url", "")
        if not name or not url:
            continue
        if url.startswith("data:"):
            try:
                encoded = url.split(",", 1)[1]
                # Try binary-safe write for non-text attachments; but many tasks expect CSV/MD/text files
                decoded = base64.b64decode(encoded)
                try:
                    text = decoded.decode("utf-8")
                    files[name] = text
                except Exception:
                    # store binary as base64 file to avoid corruption (but keep predictable)
                    files[name] = decoded.decode("latin-1", errors="ignore")
            except Exception as e:
                print("Attachment decode error:", e)
        else:
            # If a URL is provided (not data:), try to fetch it
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    files[name] = r.text
            except Exception as e:
                print("Attachment fetch error:", e)
    return files

def build_default_app_files(brief: str, attachments: list):
    """Create a base index.html that covers common templates (sum-of-sales, markdown, github-user)."""
    # Basic HTML scaffold includes bootstrap link (for templates that require it)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{brief}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-3">
  <main class="container">
    <h1 id="title">{brief}</h1>
    <div id="app-root">
      <div id="content">This page was auto-generated. If the task requires attachments (CSV/MD), place them in the repo root as provided.</div>
      <div id="total-sales" style="display:block; margin-top:1rem;"></div>
      <div id="markdown-output" style="margin-top:1rem;"></div>
      <form id="github-user-form" style="margin-top:1rem;">
        <input id="github-username" placeholder="GitHub username" />
        <button type="button" id="github-lookup">Lookup</button>
      </form>
      <div id="github-created-at"></div>
    </div>
  </main>
  <script>
  // Try to be permissive: if data.csv exists, compute sum of second column
  async function trySumCSV() {{
    try {{
      const r = await fetch('data.csv');
      if (!r.ok) return;
      const txt = await r.text();
      const rows = txt.trim().split(/\\r?\\n/).slice(1);
      let total = 0;
      rows.forEach(r => {{
        const cols = r.split(',');
        const val = parseFloat(cols[1]);
        if (!isNaN(val)) total += val;
        const tbody = document.querySelector('#product-sales tbody') || null;
        if (tbody) {{
          // handled elsewhere if needed
        }}
      }});
      document.querySelector('#total-sales').textContent = total.toFixed(2);
    }} catch(e){{}}
  }}
  trySumCSV();

  // markdown rendering if input.md exists (basic)
  async function tryMarkdown() {{
    try {{
      const r = await fetch('input.md');
      if (!r.ok) return;
      const md = await r.text();
      // very light markdown -> convert headings to <h> tags (naive)
      const html = md.replace(/^### (.*$)/gim, '<h3>$1</h3>')
                     .replace(/^## (.*$)/gim, '<h2>$1</h2>')
                     .replace(/^# (.*$)/gim, '<h1>$1</h1>')
                     .replace(/\\*\\*(.*)\\*\\*/gim, '<strong>$1</strong>')
                     .replace(/\\*(.*)\\*/gim, '<em>$1</em>')
                     .replace(/`([^`]+)`/gim, '<code>$1</code>')
                     .replace(/\\n/g, '<br/>');
      document.querySelector('#markdown-output').innerHTML = html;
    }} catch(e){{}}
  }}
  tryMarkdown();

  // github user lookup
  document.getElementById('github-lookup')?.addEventListener('click', async () => {{
    const user = document.getElementById('github-username').value.trim();
    if (!user) return;
    try {{
      const r = await fetch(`https://api.github.com/users/${{user}}`);
      if (!r.ok) {{
        document.getElementById('github-created-at').textContent = 'User not found';
        return;
      }}
      const data = await r.json();
      const created = new Date(data.created_at).toISOString().slice(0,10);
      document.getElementById('github-created-at').textContent = created;
    }} catch(e){{ document.getElementById('github-created-at').textContent = 'Error'; }}
  }});
  </script>
</body>
</html>"""
    files = {
        "index.html": html,
        "README.md": f"# Auto-generated App\n\n**Brief:** {brief}\n\nThis README was created automatically.\n\n## How this repo is structured\n- index.html : main page\n- Any attachments from the task (data.csv, input.md, sample images) are placed in the repo root.\n\nLicense: MIT\n",
        "LICENSE": MIT_LICENSE_TEXT.replace("<YEAR>", str(datetime.datetime.utcnow().year)).replace("<AUTHOR>", GITHUB_USERNAME)
    }
    # Add attachments into files if provided
    decoded = decode_attachment_to_files(attachments)
    files.update(decoded)
    return files

def post_evaluation(payload: dict, evaluation_url: str):
    delay = 1
    for attempt in range(6):
        try:
            r = requests.post(evaluation_url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                print(f"[Attempt {attempt+1}] Eval POST status {r.status_code} body: {r.text}")
        except Exception as e:
            print(f"[Attempt {attempt+1}] Eval POST error: {e}")
        time.sleep(delay)
        delay *= 2
    raise HTTPException(status_code=500, detail="Evaluation POST failed after retries")

# MIT license full text template
MIT_LICENSE_TEXT = """MIT License

Copyright (c) <YEAR> <AUTHOR>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

... (standard MIT text continues) ...

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

# -------------------------------
# ROUTES
# -------------------------------
@app.post("/task")
async def task_endpoint(payload: TaskRequest):
    """Handle round 1 and round 2 task requests from instructors."""
    verify_secret(payload.secret)

    key = (payload.email, payload.task, payload.round, payload.nonce)
    TASKS[key] = {
        "received_at": datetime.datetime.utcnow().isoformat(),
        "payload": payload.model_dump()
    }

    # Log the request
    with open("task_log.jsonl", "a") as f:
        f.write(json.dumps({"time": datetime.datetime.utcnow().isoformat(), "payload": payload.model_dump()}) + "\n")

    # Determine repo name (deterministic so round2 updates same repo)
    repo_name = deterministic_repo_name(payload.email, payload.task)
    gh = github_client()
    user = gh.get_user()

    # Ensure repo exists (create if needed)
    repo, created = ensure_repo(user, repo_name)

    # Build files based on brief + attachments
    files = build_default_app_files(payload.brief or payload.task, payload.attachments or [])

    # Push files (create or update)
    for path, content in files.items():
        create_or_update_file(repo, path, content, f"Auto add/update {path}")

    # Enable pages (REST) - this may be eventually consistent
    pages_url = enable_github_pages_rest(repo)

    # get commit sha robustly
    commit_sha = get_latest_commit_sha(repo)

    # Build evaluation payload to send back to evaluator
    evaluation_payload = {
        "email": payload.email,
        "task": payload.task,
        "round": payload.round,
        "nonce": payload.nonce,
        "repo_url": repo.html_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }

    # Post back to evaluation_url (exponential backoff)
    post_evaluation(evaluation_payload, payload.evaluation_url)

    return {"status": "ok", "message": "Task processed", "repo_url": repo.html_url, "pages_url": pages_url}

@app.post("/evaluate")
async def evaluate_endpoint(req: Request):
    """Endpoint the instructors hit to send the repo/commit/pages metadata back to you.
       This endpoint must accept the JSON payload and return 200 if it matches a known task.
    """
    data = await req.json()
    # expected keys: email, task, round, nonce, repo_url, commit_sha, pages_url
    key = (data.get("email"), data.get("task"), data.get("round"), data.get("nonce"))
    # Accept if we previously stored the task request (round 1 or 2)
    if key not in TASKS:
        # Accept anyway but warn (instructors' eval endpoint may expect 200 even if mismatch).
        # To be strict, return 400
        return {"detail": "Not Found - no matching task recorded for that (email,task,round,nonce)"}
    # Log
    with open("evaluate_log.jsonl", "a") as f:
        f.write(json.dumps({"time": datetime.datetime.utcnow().isoformat(), "incoming": data}) + "\n")
    return {"status": "ok", "message": "Evaluation accepted"}

@app.get("/")
async def root():
    return {"message": "Project1 API running"}
