from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Protocol
from urllib.parse import urlencode


class StorageError(RuntimeError):
    pass


class StorageNotFoundError(StorageError):
    pass


class StorageUnavailableError(StorageError):
    pass


class UploadLimitError(ValueError):
    pass


class UploadAuthorizationError(ValueError):
    pass


@dataclass(frozen=True)
class UploadTarget:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: int


class StorageProvider(Protocol):
    backend_name: str

    def create_upload_url(
        self,
        object_key: str,
        *,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        content_type: str,
        expires_in: int,
        upload_endpoint: str | None = None,
    ) -> UploadTarget: ...

    def download_to_temp(self, object_key: str) -> Path: ...

    def delete_object(self, object_key: str) -> None: ...

    def object_exists(self, object_key: str) -> bool: ...

    def object_size(self, object_key: str) -> int: ...

    def release_temp(self, path: Path) -> None: ...


def build_object_key(workspace_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"workspaces/{workspace_id}/documents/{document_id}.pdf"


def validate_object_scope(object_key: str, workspace_id: uuid.UUID, document_id: uuid.UUID) -> None:
    if object_key != build_object_key(workspace_id, document_id):
        raise UploadAuthorizationError("对象存储路径与当前工作区不一致")


class UploadURLSigner:
    def __init__(self, signing_key: str) -> None:
        self._key = signing_key.encode("utf-8")

    def issue(self, workspace_id: uuid.UUID, document_id: uuid.UUID, object_key: str, expires_at: int) -> str:
        payload = {
            "workspace_id": str(workspace_id),
            "document_id": str(document_id),
            "object_key": object_key,
            "expires_at": expires_at,
        }
        encoded = self._encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = self._encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str) -> tuple[uuid.UUID, uuid.UUID, str]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = self._encode(hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(expected, supplied):
                raise UploadAuthorizationError("上传地址签名无效")
            payload = json.loads(self._decode(encoded))
            if int(payload["expires_at"]) < int(time.time()):
                raise UploadAuthorizationError("上传地址已过期，请重新获取")
            workspace_id = uuid.UUID(payload["workspace_id"])
            document_id = uuid.UUID(payload["document_id"])
            object_key = str(payload["object_key"])
            validate_object_scope(object_key, workspace_id, document_id)
            return workspace_id, document_id, object_key
        except UploadAuthorizationError:
            raise
        except Exception as exc:
            raise UploadAuthorizationError("上传地址无效") from exc

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class LocalStorageProvider:
    backend_name = "local"

    def __init__(
        self,
        root: Path,
        *,
        max_upload_bytes: int,
        signing_key: str = "revia-local-upload-signing-key-change-me",
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes
        self.signer = UploadURLSigner(signing_key)

    def create_upload_url(
        self,
        object_key: str,
        *,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        content_type: str,
        expires_in: int,
        upload_endpoint: str | None = None,
    ) -> UploadTarget:
        if upload_endpoint is None:
            raise StorageError("本地存储需要后端上传地址")
        validate_object_scope(object_key, workspace_id, document_id)
        expires_at = int(time.time()) + expires_in
        token = self.signer.issue(workspace_id, document_id, object_key, expires_at)
        separator = "&" if "?" in upload_endpoint else "?"
        return UploadTarget(
            url=f"{upload_endpoint}{separator}{urlencode({'token': token})}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=expires_at,
        )

    async def save_stream(self, object_key: str, stream: AsyncIterator[bytes]) -> int:
        path = self.resolve(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".uploading")
        size = 0
        try:
            with temporary.open("wb") as target:
                async for data in stream:
                    if not data:
                        continue
                    size += len(data)
                    if size > self.max_upload_bytes:
                        raise UploadLimitError(
                            f"PDF 文件不能超过 {self.max_upload_bytes // (1024 * 1024)}MB"
                        )
                    target.write(data)
            temporary.replace(path)
            return size
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    async def save_pdf(self, object_key: str, file: object) -> tuple[str, int]:
        async def chunks() -> AsyncIterator[bytes]:
            while data := await file.read(1024 * 1024):  # type: ignore[attr-defined]
                yield data

        return object_key, await self.save_stream(object_key, chunks())

    def resolve(self, object_key: str) -> Path:
        path = (self.root / object_key).resolve()
        if path != self.root and self.root not in path.parents:
            raise StorageError("对象存储路径越界")
        return path

    def download_to_temp(self, object_key: str) -> Path:
        path = self.resolve(object_key)
        if not path.is_file():
            raise StorageNotFoundError("原始 PDF 已不存在，无法继续解析")
        return path

    def delete_object(self, object_key: str) -> None:
        self.resolve(object_key).unlink(missing_ok=True)

    def object_exists(self, object_key: str) -> bool:
        return self.resolve(object_key).is_file()

    def object_size(self, object_key: str) -> int:
        try:
            return self.resolve(object_key).stat().st_size
        except FileNotFoundError as exc:
            raise StorageNotFoundError("原始 PDF 已不存在") from exc

    def release_temp(self, path: Path) -> None:
        return None


class S3StorageProvider:
    backend_name = "s3"

    def __init__(
        self,
        *,
        endpoint: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        region: str,
        force_path_style: bool,
        temp_root: Path,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageError("S3 兼容对象存储依赖未安装") from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "path" if force_path_style else "virtual"},
            ),
        )
        self._bucket = bucket_name
        self._temp_root = temp_root.resolve()
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def create_upload_url(
        self,
        object_key: str,
        *,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        content_type: str,
        expires_in: int,
        upload_endpoint: str | None = None,
    ) -> UploadTarget:
        validate_object_scope(object_key, workspace_id, document_id)
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
        return UploadTarget(
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=int(time.time()) + expires_in,
        )

    def download_to_temp(self, object_key: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix="revia-", suffix=".pdf", dir=self._temp_root)
        os.close(descriptor)
        path = Path(name)
        body = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
            body = response["Body"]
            expected_size = int(response.get("ContentLength") or 0)
            downloaded_size = 0
            with path.open("wb") as target:
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded_size += len(chunk)
                    target.write(chunk)
            if expected_size and downloaded_size != expected_size:
                raise StorageUnavailableError(
                    f"对象存储下载不完整（expected={expected_size}, received={downloaded_size}）"
                )
            return path
        except StorageUnavailableError:
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            if self._is_not_found(exc):
                raise StorageNotFoundError("原始 PDF 已不存在，无法继续解析") from exc
            code = self._safe_error_code(exc)
            raise StorageUnavailableError(
                f"对象存储下载暂时失败（{code}），请稍后继续识别"
            ) from exc
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def delete_object(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)

    def object_exists(self, object_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise StorageUnavailableError(f"无法检查对象存储文件（{self._safe_error_code(exc)}）") from exc

    def object_size(self, object_key: str) -> int:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=object_key)
            return int(response["ContentLength"])
        except Exception as exc:
            if self._is_not_found(exc):
                raise StorageNotFoundError("原始 PDF 已不存在") from exc
            raise StorageUnavailableError(f"无法读取对象存储文件信息（{self._safe_error_code(exc)}）") from exc

    def release_temp(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound"}

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", "")).strip()
        if code:
            return code[:80]
        return exc.__class__.__name__[:80]


def build_storage_provider(settings: object) -> StorageProvider:
    backend = str(getattr(settings, "storage_backend"))
    if backend == "local":
        return LocalStorageProvider(
            Path(str(getattr(settings, "file_storage_root"))),
            max_upload_bytes=int(getattr(settings, "max_upload_mb")) * 1024 * 1024,
            signing_key=str(getattr(settings, "session_signing_key")),
        )
    if backend == "s3":
        return S3StorageProvider(
            endpoint=str(getattr(settings, "s3_endpoint")),
            region=str(getattr(settings, "s3_region")),
            access_key_id=str(getattr(settings, "s3_access_key_id")),
            secret_access_key=str(getattr(settings, "s3_secret_access_key")),
            bucket_name=str(getattr(settings, "s3_bucket_name")),
            force_path_style=bool(getattr(settings, "s3_force_path_style")),
            temp_root=Path(str(getattr(settings, "file_storage_root"))),
        )
    raise StorageError(f"不支持的 STORAGE_BACKEND：{backend}")


# Backward-compatible name for tests and local integrations.
LocalFileStorage = LocalStorageProvider
