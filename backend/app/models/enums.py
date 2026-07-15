from enum import StrEnum


class ProjectStatus(StrEnum):
    NOT_UPLOADED = "not_uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    PUBLIC = "public"


class DocumentKind(StrEnum):
    COURSE_MATERIAL = "course_material"
    SYLLABUS = "syllabus"


class DocumentProcessingStatus(StrEnum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    PARSING = "parsing"
    INTERRUPTED = "interrupted"
    PARSED = "parsed"
    FAILED = "failed"


class DocumentPageStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionMethod(StrEnum):
    TEXT = "text"
    OCR = "ocr"


class GenerationStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    MATCHING = "matching"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class ContentVersionKind(StrEnum):
    ORIGINAL = "original"
    RECITATION = "recitation"
    KEYWORDS = "keywords"
