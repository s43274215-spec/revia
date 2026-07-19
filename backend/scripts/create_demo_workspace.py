"""Plan or create one empty, isolated Demo Workspace.

Dry-run is the default. This script never copies projects or production content.
Run from the backend directory with DATABASE_URL configured for the intended database.
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.config import get_settings
from app.models.enums import WorkspaceRole
from app.models.workspace import Workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an empty isolated Revia Demo Workspace")
    parser.add_argument("--apply", action="store_true", help="perform the insert; otherwise only print the plan")
    args = parser.parse_args()
    workspace_id = uuid.uuid4()
    if not args.apply:
        print("DRY RUN: would create one empty public-role Workspace for DEMO_WORKSPACE_ID.")
        print("No projects, documents, learning materials, or production data would be copied.")
        return
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as db:
            db.add(Workspace(id=workspace_id, role=WorkspaceRole.PUBLIC))
            db.commit()
        print(f"Created empty Demo Workspace: {workspace_id}")
        print("Set DEMO_WORKSPACE_ID to this value only after adding reviewed demo content separately.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
