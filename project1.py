# project1.py
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from github import Github
import os, uuid, base64, requests, datetime, time
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
# FASTAPI APP
# -------------------------------
app = FastAPI(title="Project 1 API")

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

# -------------------------------
# HELPERS
# -------------------------------
def verify_secret(secret: str):
    if secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

def create_or_get_repo(task_name: str):
    """Create a new public GitHub repo or reuse if exists."""
    g = Github(GITHUB_TOKEN)
    user = g.get_user()
    repo_name = f"{task_name.lower()}-{uuid.uuid4().hex[:6]}"
    repo = user.create_repo(
        name=repo_name,
        private=False,
        description=f"Auto-generated repo for task: {task_name}",
        auto_init=False
    )
    return repo

def push_files_to_repo(repo, files: dict):
    """Push files to GitHub repo; create or update."""
    for path, content in files.items():
        try:
            # Check if file exists
            try:
                existing_file = repo.get_contents(path)
                repo.update_file(path, f"Update {path}", content, existing_file.sha)
            except:
                repo.create_file(path, f"Add {path}", content)
        except Exception as e:
            print(f"Error pushing {path}: {e}")

def enable_github_pages(repo):
    """Enable GitHub Pages (gh-pages branch)"""
    try:
        repo.edit(has_pages=True)
        return f"https://{GITHUB_USERNAME}.github.io/{repo.name}/"
    except Exception as e:
        print("Error enabling GitHub Pages:", e)
        return None

def generate_app_files(brief: str, attachments: list):
    """Minimal HTML + README + LICENSE, include attachments."""
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
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

def post_evaluation(payload: dict, evaluation_url: str):
    """Post the evaluation payload with exponential backoff."""
    delay = 1
    for _ in range(5):
        try:
            r = requests.post(evaluation_url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            else:
                print(f"Eval POST failed: {r.status_code} -> {r.text}")
        except Exception as e:
            print(f"Eval POST error: {e}")
        time.sleep(delay)
        delay *= 2
    raise HTTPException(status_code=500, detail="Evaluation POST failed after retries")

# -------------------------------
# /task ENDPOINT
# -------------------------------
@app.post("/task")
async def handle_task(request: TaskRequest):
    verify_secret(request.secret)

    # Log task request
    with open("task_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {request.model_dump_json()}\n")

    # Generate files
    files_to_push = generate_app_files(request.brief, request.attachments)

    # Create GitHub repo
    repo = create_or_get_repo(request.task)

    # Push files
    push_files_to_repo(repo, files_to_push)

    # Enable Pages
    pages_url = enable_github_pages(repo)

    # Get latest commit SHA
    commit_sha = None
    try:
        commit_sha = repo.get_commits()[0].sha
    except Exception as e:
        print("Error fetching commit SHA:", e)

    # Build payload for evaluator
    evaluation_payload = {
        "email": request.email,
        "task": request.task,
        "round": request.round,
        "nonce": request.nonce,
        "repo_url": repo.html_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }

    # Post to evaluation URL
    try:
        post_evaluation(evaluation_payload, request.evaluation_url)
    except Exception as e:
        print(f"Warning: could not post to evaluation URL: {e}")

    return {
        "status": "ok",
        "message": "Task received successfully",
        "repo_url": repo.html_url,
        "pages_url": pages_url
    }

# -------------------------------
# /evaluate ENDPOINT
# -------------------------------
@app.post("/evaluate")
async def evaluate(request: Request):
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # Log evaluation payload
    with open("evaluation_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {payload}\n")

    return {"status": "ok", "message": "Evaluation received successfully"}
