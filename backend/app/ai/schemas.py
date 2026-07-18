import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ContentVersionKind

TITLE_MAX_LENGTH = 25
ORIGINAL_MAX_LENGTH = 800
RECITATION_MAX_LENGTH = 400
KEYWORDS_MAX_LENGTH = 160

_HEADING_NUMBER_PATTERN = re.compile(
    r"第[一二三四五六七八九十百\d]+[章节篇]|(?:^|\s)[一二三四五六七八九十百]+[、.]|[（(][一二三四五六七八九十百\d]+[）)]"
)
_OCR_NOISE_MARKERS = (
    "严禁复制",
    "复制此链接",
    "mininunversity",
    "hinannvvesiy",
    "qkc:/",
    "http://",
    "https://",
)
_COLLECTION_TITLE_PATTERN = re.compile(r"(?:特征|特点|类型|原则|步骤|流程|因素|模块|构成|内容|方法)(?:是|为|包括|包含|如下|[：:]?\s*$)")
_ORDERED_ITEM_PATTERN = re.compile(r"(?:^|\n)\s*(?:\d+[.、)）]|[（(][一二三四五六七八九十\d]+[）)])\s*")


def _normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value, flags=re.UNICODE).casefold()


def _validate_meaningful_text(value: str) -> str:
    cleaned = value.strip()
    if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned)) < 2:
        raise ValueError("text must contain meaningful readable content")
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in _OCR_NOISE_MARKERS):
        raise ValueError("text contains OCR noise, duplicated watermark, or an unrelated link")
    return cleaned


def _validate_generated_title(value: str) -> str:
    cleaned = _validate_meaningful_text(value)
    if len(cleaned) > TITLE_MAX_LENGTH:
        raise ValueError(f"title must not exceed {TITLE_MAX_LENGTH} characters")
    if len(_HEADING_NUMBER_PATTERN.findall(cleaned)) > 1:
        raise ValueError("title must not combine multiple heading-number levels")
    return cleaned


class GeneratedContentVersion(BaseModel):
    kind: ContentVersionKind
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


class GeneratedBulletPoint(BaseModel):
    id: uuid.UUID
    versions: list[GeneratedContentVersion]

    @model_validator(mode="after")
    def contains_all_versions(self) -> "GeneratedBulletPoint":
        expected = set(ContentVersionKind)
        actual = {version.kind for version in self.versions}
        if actual != expected or len(self.versions) != len(expected):
            raise ValueError("A bullet point must contain exactly one original, recitation, and keywords version")
        return self


class GeneratedKnowledgePoint(BaseModel):
    id: uuid.UUID
    title: str = Field(min_length=1)
    bullet_points: list[GeneratedBulletPoint] = Field(min_length=1)


class GeneratedChapter(BaseModel):
    id: uuid.UUID
    title: str = Field(min_length=1)
    knowledge_points: list[GeneratedKnowledgePoint] = Field(min_length=1)


class GeneratedProject(BaseModel):
    project_id: uuid.UUID
    chapters: list[GeneratedChapter] = Field(min_length=1)


class GeneratedVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    content: str = Field(min_length=1, max_length=ORIGINAL_MAX_LENGTH)

    @field_validator("title")
    @classmethod
    def title_is_readable_and_concise(cls, value: str) -> str:
        return _validate_generated_title(value)

    @field_validator("content")
    @classmethod
    def content_is_readable(cls, value: str) -> str:
        return _validate_meaningful_text(value)


class GeneratedBulletPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    original: GeneratedVersionPayload
    recitation: GeneratedVersionPayload
    keywords: GeneratedVersionPayload
    source_chunk_ids: list[uuid.UUID] = Field(min_length=1)
    source_pages: list[int] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def title_is_readable_and_concise(cls, value: str) -> str:
        return _validate_generated_title(value)

    @model_validator(mode="after")
    def sources_are_unique_and_positive(self) -> "GeneratedBulletPayload":
        if len(self.source_chunk_ids) != len(set(self.source_chunk_ids)):
            raise ValueError("source_chunk_ids must be unique")
        if len(self.source_pages) != len(set(self.source_pages)) or any(page < 1 for page in self.source_pages):
            raise ValueError("source_pages must contain unique positive page numbers")
        version_titles = {self.original.title, self.recitation.title, self.keywords.title}
        if version_titles != {self.title}:
            raise ValueError("bullet title and all three version titles must be identical")
        if len(self.recitation.content) > RECITATION_MAX_LENGTH:
            raise ValueError(f"recitation content must not exceed {RECITATION_MAX_LENGTH} characters")
        if len(self.keywords.content) > KEYWORDS_MAX_LENGTH:
            raise ValueError(f"keywords content must not exceed {KEYWORDS_MAX_LENGTH} characters")
        keyword_items = [
            item.strip()
            for item in re.split(r"[\n、，,；;]+", self.keywords.content)
            if item.strip()
        ]
        if not 3 <= len(keyword_items) <= 8:
            raise ValueError("keywords content must contain between 3 and 8 keywords or phrases")
        normalized_keywords = [_normalize_title(item) for item in keyword_items]
        if len(normalized_keywords) != len(set(normalized_keywords)):
            raise ValueError("keywords content must not contain duplicate keywords or phrases")
        if self.original.content.strip() == self.recitation.content.strip():
            raise ValueError("recitation content must not duplicate original content verbatim")
        return self


class GeneratedItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_point_title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    bullet_points: list[GeneratedBulletPayload] = Field(min_length=1)

    @field_validator("knowledge_point_title", mode="before")
    @classmethod
    def reduce_long_topic_lists_to_their_subject(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if len(cleaned) <= TITLE_MAX_LENGTH:
            return cleaned
        subject = re.split(r"[：:]", cleaned, maxsplit=1)[0].strip()
        return subject if 1 < len(subject) <= TITLE_MAX_LENGTH else cleaned

    @field_validator("knowledge_point_title")
    @classmethod
    def title_is_readable_and_concise(cls, value: str) -> str:
        return _validate_generated_title(value)

    @model_validator(mode="after")
    def titles_form_a_non_repeating_hierarchy(self) -> "GeneratedItemResult":
        parent = _normalize_title(self.knowledge_point_title)
        child_titles: list[str] = []
        for bullet in self.bullet_points:
            child = _normalize_title(bullet.title)
            if parent and parent in child:
                raise ValueError("bullet title must not contain the complete knowledge point title")
            child_titles.append(child)
        if len(child_titles) != len(set(child_titles)):
            raise ValueError("bullet point titles must be unique within a knowledge point")
        if _COLLECTION_TITLE_PATTERN.search(self.knowledge_point_title) and len(self.bullet_points) == 1:
            original = self.bullet_points[0].original.content
            if len(_ORDERED_ITEM_PATTERN.findall(original)) >= 2:
                raise ValueError("collection items must be returned as separate bullet points")
        return self
