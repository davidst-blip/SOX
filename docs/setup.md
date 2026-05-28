# SOX Sentinel — Local Setup

## Prerequisites

- Python 3.11+
- Docker Desktop (for Postgres)
- Git

## First-time setup

```powershell
# 1. Clone the repo
git clone https://github.com/davidst-blip/SOX.git sox-sentinel
cd sox-sentinel

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
copy .env.example .env
# Edit .env and fill in your ANTHROPIC_API_KEY

# 5. Start Postgres
docker-compose up -d

# 6. Run DB migrations
alembic upgrade head

# 7. Start the API
uvicorn backend.main:app --reload
```

The API will be available at http://localhost:8000  
Auto-generated docs: http://localhost:8000/docs

## Running tests

```powershell
pytest tests/ -v
```

## Real Perion documents

Never commit real Perion SOX documents to this repo.  
Place test files in `samples/real/` — that directory is gitignored.

## VS Code

Open the `sox-sentinel` folder in VS Code. The `.vscode/settings.json` file configures the Python interpreter, formatter (Ruff), and test runner automatically.
