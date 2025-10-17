from fastapi import FastAPI, HTTPException
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
# FASTAPI APP INIT
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
    """Verify that the incoming secret matches the server secret."""
    if secret != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")


def create_github_repo(task_name: str):
    """Create a new public GitHub repository using the provided token."""
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
    """Push generated files to the GitHub repository."""
    for path, content in files.items():
        try:
            repo.create_file(path, f"Add {path}", content)
        except Exception as e:
            print(f"Error pushing {path}: {e}")


def enable_github_pages(repo):
    """Enable GitHub Pages and return the public URL."""
    try:
        # Create gh-pages branch if needed
        ref_list = [r.ref for r in repo.get_git_refs()]
        if "refs/heads/gh-pages" not in ref_list:
            master_ref = repo.get_git_ref("heads/main") if "refs/heads/main" in ref_list else repo.get_git_ref("heads/master")
            repo.create_git_ref(ref="refs/heads/gh-pages", sha=master_ref.object.sha)
        repo.edit(default_branch="gh-pages")
        return f"https://{GITHUB_USERNAME}.github.io/{repo.name}/"
    except Exception as e:
        print("Error enabling GitHub Pages:", e)
        return None


def generate_app_files(brief: str, attachments: list):
    """Generate minimal HTML + README + LICENSE files, plus attachments."""
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


def post_evaluation(payload: dict, evaluation_url: str):
    """Post the evaluation payload with exponential backoff retries."""
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
    raise HTTPException(status_code=500, detail="Evaluation POST failed after retries")

# -------------------------------
# MAIN ENDPOINT
# -------------------------------
@app.post("/task")
async def handle_task(request: TaskRequest):
    """Main endpoint for receiving and processing task requests."""
    verify_secret(request.secret)

    # Log locally
    with open("task_log.jsonl", "a") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {request.model_dump_json()}\n")

    # Generate app files
    files_to_push = generate_app_files(request.brief, request.attachments)

    # Create GitHub repo
    repo = create_github_repo(request.task)

    # Push files
    push_files_to_repo(repo, files_to_push)

    # Enable Pages and get URL
    pages_url = enable_github_pages(repo)

    # Get commit SHA
    commit_sha = None
    try:
        commit_sha = repo.get_commits()[0].sha
    except Exception as e:
        print("Error fetching commit SHA:", e)

    # Build evaluation payload
    evaluation_payload = {
        "email": request.email,
        "task": request.task,
        "round": request.round,
        "nonce": request.nonce,
        "repo_url": repo.html_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }

    # Send to evaluator
    post_evaluation(evaluation_payload, request.evaluation_url)

    return {"status": "ok", "message": "Task received and processed successfully"}
