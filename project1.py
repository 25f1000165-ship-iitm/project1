# project1.py
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from github import Github
import os, uuid, base64, requests, datetime, time, json
from dotenv import load_dotenv

# -------------------------------
# LOAD ENVIRONMENT VARIABLES
# -------------------------------
load_dotenv()
SECRET = os.getenv("SECRET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")

if not SECRET or not GITHUB_TOKEN or not GITHUB_USERNAME:
    raise Exception("Set SECRET, GITHUB_TOKEN, and GITHUB_USERNAME in .env")

# -------------------------------
# FASTAPI APP INIT
# -------------------------------
app = FastAPI(title="Project1 API")

# -------------------------------
# REQUEST MODEL
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

class EvaluateRequest(BaseModel):
    email: str
    task: str
    round: int
    nonce: str
    repo_url: str
    commit_sha: str
    pages_url: str

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def verify_secret(secret: str):
    if secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

def get_github_repo(task_name: str, email: str):
    """Return existing repo if exists, else None"""
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    repos = list(user.get_repos())
    prefix = f"{task_name.lower()}-{email.replace('@','-').replace('.','-')}"
    for repo in repos:
        if repo.name.startswith(prefix):
            return repo
    return None

def create_github_repo(task_name: str, email: str):
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    repo_name = f"{task_name.lower()}-{email.replace('@','-').replace('.','-')}-{uuid.uuid4().hex[:5]}"
    repo = user.create_repo(
        name=repo_name,
        private=False,
        description=f"Auto-generated repo for task {task_name}",
        auto_init=False
    )
    return repo

def create_or_update_file(repo, path, content):
    """Create a new file or update existing file using SHA"""
    try:
        existing_file = None
        try:
            existing_file = repo.get_contents(path)
        except:
            existing_file = None
        if existing_file:
            repo.update_file(path, f"Update {path}", content, existing_file.sha)
        else:
            repo.create_file(path, f"Add {path}", content)
    except Exception as e:
        print(f"Error creating/updating {path}: {e}")

def generate_app_files(brief: str, attachments: list):
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{brief}</title>
    </head>
    <body>
        <h1>{brief}</h1>
        <p>This project was automatically generated.</p>
    </body>
    </html>
    """
    files = {
        "index.html": html_content.strip(),
        "README.md": f"# Auto-generated App\n\n**Brief:** {brief}\n\nLicense: MIT",
        "LICENSE": "MIT License"
    }
    for att in attachments:
        try:
            data = att.get("url")
            if data and data.startswith("data:"):
                encoded = data.split(",")[1]
                content = base64.b64decode(encoded).decode("utf-8", errors="ignore")
                files[att["name"]] = content
        except Exception as e:
            print(f"Error decoding attachment {att.get('name')}: {e}")
    return files

def enable_github_pages(repo):
    try:
        repo.edit(has_pages=True)
        return f"https://{GITHUB_USERNAME}.github.io/{repo.name}/"
    except Exception as e:
        print("Error enabling GitHub Pages:", e)
        return None

def post_evaluation(payload: dict, evaluation_url: str):
    delay = 1
    for _ in range(5):
        try:
            r = requests.post(evaluation_url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                print(f"Evaluation POST failed: {r.status_code} -> {r.text}")
        except Exception as e:
            print(f"Evaluation POST error: {e}")
        time.sleep(delay)
        delay *= 2
    print("Evaluation POST failed after retries")

# -------------------------------
# ENDPOINTS
# -------------------------------
@app.post("/task")
async def handle_task(request: TaskRequest):
    verify_secret(request.secret)
    # Log task
    with open("task_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {request.model_dump_json()}\n")

    # Get or create repo
    repo = get_github_repo(request.task, request.email)
    if not repo:
        repo = create_github_repo(request.task, request.email)

    # Generate files
    files = generate_app_files(request.brief, request.attachments)
    for path, content in files.items():
        create_or_update_file(repo, path, content)

    # Enable Pages
    pages_url = enable_github_pages(repo)

    # Get latest commit SHA
    try:
        commit_sha = repo.get_commits()[0].sha
    except:
        commit_sha = None

    # Post evaluation
    evaluation_payload = {
        "email": request.email,
        "task": request.task,
        "round": request.round,
        "nonce": request.nonce,
        "repo_url": repo.html_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }
    post_evaluation(evaluation_payload, request.evaluation_url)

    return {"status": "ok", "message": "Task received successfully", "repo_url": repo.html_url, "pages_url": pages_url}

@app.post("/evaluate")
async def evaluate(request: EvaluateRequest):
    # Log evaluation
    with open("evaluation_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {request.model_dump_json()}\n")
    return {"status": "ok", "message": "Evaluation recorded successfully"}
