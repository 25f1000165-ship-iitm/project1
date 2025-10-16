# project1.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from github import Github
import os, uuid, base64, requests, datetime
from dotenv import load_dotenv
import time

# -------------------------------
# LOAD ENV
# -------------------------------
load_dotenv()
SECRET = os.getenv("SECRET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER")

if not SECRET or not GITHUB_TOKEN or not GITHUB_USER:
    raise Exception("Set SECRET, GITHUB_TOKEN, and GITHUB_USER in .env")

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

# -------------------------------
# APP
# -------------------------------
app = FastAPI(title="Project 1 API")

# -------------------------------
# HELPERS
# -------------------------------
def verify_secret(secret: str):
    if secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

def create_github_repo(task_name: str):
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    repo_name = f"{task_name}-{uuid.uuid4().hex[:5]}"
    repo = user.create_repo(
        name=repo_name,
        private=False,
        description=f"Auto-generated repo for {task_name}",
        auto_init=False,
        license_template="mit"
    )
    return repo

def push_files_to_repo(repo, files: dict):
    for path, content in files.items():
        repo.create_file(path, f"Add {path}", content)

def enable_github_pages(repo):
    repo.edit(has_pages=True)
    return f"https://{GITHUB_USER}.github.io/{repo.name}/"

def generate_app_files(brief: str, attachments: list):
    # Minimal HTML app for any task
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{brief}</title>
    </head>
    <body>
        <h1>{brief}</h1>
    </body>
    </html>
    """
    files = {
        "index.html": html_content,
        "README.md": f"# Auto-generated App\n\n{brief}\n\nLicense: MIT",
        "LICENSE": "MIT License..."
    }
    # Add attachments
    for att in attachments:
        try:
            data = att.get("url").split(",")[1]
            content = base64.b64decode(data).decode('utf-8', errors='ignore')
            files[att["name"]] = content
        except Exception as e:
            print(f"Error decoding attachment {att.get('name')}: {e}")
    return files

def post_evaluation(payload: dict, evaluation_url: str):
    delay = 1
    for _ in range(5):  # retries: 1,2,4,8,16 sec
        try:
            r = requests.post(evaluation_url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                print(f"Evaluation POST failed: HTTP {r.status_code}")
                raise Exception()
        except:
            time.sleep(delay)
            delay *= 2
    raise HTTPException(status_code=500, detail="Evaluation POST failed after retries")

# -------------------------------
# ENDPOINT
# -------------------------------
@app.post("/task")
async def handle_task(request: TaskRequest):
    verify_secret(request.secret)

    # Log locally
    with open("task_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now()} {request.dict()}\n")

    # Generate files
    files_to_push = generate_app_files(request.brief, request.attachments)

    # Create GitHub repo
    repo = create_github_repo(request.task)

    # Push files
    push_files_to_repo(repo, files_to_push)

    # Enable Pages
    pages_url = enable_github_pages(repo)

    # Prepare evaluation payload
    evaluation_payload = {
        "email": request.email,
        "task": request.task,
        "round": request.round,
        "nonce": request.nonce,
        "repo_url": repo.html_url,
        "commit_sha": repo.get_commits()[0].sha,
        "pages_url": pages_url
    }

    # Post to evaluator
    post_evaluation(evaluation_payload, request.evaluation_url)

    return {"status": "ok", "message": "Task received and processed successfully"}
