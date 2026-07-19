import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ContentVersionKind


class ContentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ContentVersionKind
    title: str
    content: str


class ContentVersionUpdate(BaseModel):
    kind: ContentVersionKind
    title: str = Field(min_length=1)
    content: str


class BulletPointSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text_chunk_id: uuid.UUID
    page_start: int
    page_end: int


class BulletPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    versions: list[ContentVersionRead]
    sources: list[BulletPointSourceRead] = Field(default_factory=list)


class BulletPointUpdate(BaseModel):
    versions: list[ContentVersionUpdate]

    @model_validator(mode="after")
    def versions_are_unique(self) -> "BulletPointUpdate":
        kinds = [version.kind for version in self.versions]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Each content version kind may appear only once")
        return self


class KnowledgePointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    position: int
    bullet_points: list[BulletPointRead]


class ChapterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    position: int
    chapter_resolved: bool
    knowledge_points: list[KnowledgePointRead]


class LearningMaterialRead(BaseModel):
    project_id: uuid.UUID
    chapters: list[ChapterRead]
