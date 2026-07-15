"""Disposable public-mode server used by the Playwright acceptance check."""

import os
import sys
import tempfile
from pathlib import Path

backend_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_root))

runtime_root = Path(tempfile.gettempdir()) / f"revia-public-browser-{os.getpid()}"
runtime_root.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "PUBLIC_ACCESS_ENABLED": "true",
    "DATABASE_URL": f"sqlite+pysqlite:///{(runtime_root / 'revia.sqlite3').as_posix()}",
    "FILE_STORAGE_ROOT": str(runtime_root / "storage"),
    "STORAGE_BACKEND": "local",
})

import app.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402

Base.metadata.create_all(engine)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
