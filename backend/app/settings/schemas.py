from datetime import datetime

from pydantic import BaseModel, Field


class DeepSeekConfigurationStatus(BaseModel):
    configured: bool
    masked_hint: str | None = None


class TransportPublicKeyRead(BaseModel):
    algorithm: str = "RSA-OAEP-256"
    public_key: str


class EncryptedAPIKeyWrite(BaseModel):
    encrypted_api_key: str = Field(min_length=1, max_length=4096)


class DeepSeekConnectionTestRequest(BaseModel):
    encrypted_api_key: str | None = Field(default=None, min_length=1, max_length=4096)


class DeepSeekSettingsResult(BaseModel):
    configured: bool
    masked_hint: str | None = None
    message: str


class DeepSeekConnectionResult(BaseModel):
    success: bool
    message: str


class SiteSettingsRead(BaseModel):
    public_access_enabled: bool
    updated_at: datetime


class SiteSettingsUpdate(BaseModel):
    public_access_enabled: bool
