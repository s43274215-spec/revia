import uuid

from pydantic import BaseModel, Field


class AccessCodeRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=256)


class WorkspaceSessionRead(BaseModel):
    token: str
    workspace_id: uuid.UUID


class WorkspaceRead(BaseModel):
    workspace_id: uuid.UUID
