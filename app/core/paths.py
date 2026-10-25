"""Drive-agnostic path resolution.

Everything is resolved relative to the project root so the folder can live on
any drive letter (E:\\, F:\\, ...) or be moved between machines unchanged.
"""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]          # .../app
ROOT = APP_DIR.parent                                   # .../PortableAI
CORE_DIR = Path(__file__).resolve().parent

WEB_DIR = APP_DIR / "web"
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = ROOT / "data"
INDEX_DIR = DATA_DIR / "index"
REPORTS_DIR = DATA_DIR / "reports"
UPLOADS_DIR = DATA_DIR / "uploads"
LOGS_DIR = ROOT / "logs"
MODELS_DIR = ROOT / "models"
RUNTIME_DIR = ROOT / "runtime"
LLAMA_DIR = RUNTIME_DIR / "llama"
DOCS_DIR = ROOT / "docs"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, INDEX_DIR, REPORTS_DIR, UPLOADS_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def rel(p) -> str:
    """Path relative to ROOT when possible, else absolute (for display)."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except Exception:
        return str(p)


def resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / q)
