import uuid
from pathlib import Path

from fastapi import UploadFile


class LocalFileStorage:
    def __init__(self, root: Path, *, max_upload_bytes: int) -> None:
        self.root = root.resolve()
        self.max_upload_bytes = max_upload_bytes

    async def save_pdf(self, project_id: uuid.UUID, document_id: uuid.UUID, file: UploadFile) -> tuple[str, int]:
        directory = self.root / str(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{document_id}.pdf"
        size = 0
        try:
            with path.open("wb") as target:
                while data := await file.read(1024 * 1024):
                    size += len(data)
                    if size > self.max_upload_bytes:
                        raise UploadLimitError(f"PDF 文件不能超过 {self.max_upload_bytes // (1024 * 1024)}MB")
                    target.write(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path.relative_to(self.root).as_posix(), size

    def resolve(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise ValueError("Storage key escapes the configured storage root")
        return path

    def delete(self, storage_key: str) -> None:
        self.resolve(storage_key).unlink(missing_ok=True)


class UploadLimitError(ValueError):
    pass
