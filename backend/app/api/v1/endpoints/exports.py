import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import WorkspaceId
from app.db.session import get_db
from app.models.enums import ContentVersionKind
from app.services.word_export import WordExportNotFoundError, WordExportService, content_disposition

router = APIRouter()


@router.get("/{project_id}/exports/word")
def export_word(
    project_id: uuid.UUID,
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
    version: Annotated[Literal["original", "recitation", "keywords", "all"], Query()] = "all",
) -> StreamingResponse:
    selected = None if version == "all" else ContentVersionKind(version)
    try:
        payload, filename = WordExportService(db).export(workspace_id, project_id, selected)
    except WordExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StreamingResponse(
        payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(filename)},
    )
