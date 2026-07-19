import uuid

from pydantic import BaseModel, Field


class AccessCodeRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=256)


class WorkspaceSessionRead(BaseModel):
    workspace_id: uuid.UUID
    role: str


class WorkspaceRead(BaseModel):
    workspace_id: uuid.UUID
    role: str


class AccessModeRead(BaseModel):
    public_access_enabled: bool
    demo_access_enabled: bool
