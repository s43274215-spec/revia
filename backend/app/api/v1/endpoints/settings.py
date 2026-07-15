from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import OwnerWorkspace, WorkspaceId
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.settings.schemas import (
    DeepSeekConfigurationStatus,
    DeepSeekConnectionResult,
    DeepSeekConnectionTestRequest,
    DeepSeekSettingsResult,
    EncryptedAPIKeyWrite,
    SiteSettingsRead,
    SiteSettingsUpdate,
    TransportPublicKeyRead,
)
from app.settings.security import CredentialCipher, SecretStorageError, SecretTransportError, get_transport_key_pair
from app.settings.service import DeepSeekSettingsService
from app.settings.site_service import SiteSettingsService

router = APIRouter()


def get_deepseek_settings_service(
    workspace_id: WorkspaceId,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeepSeekSettingsService:
    return DeepSeekSettingsService(
        db=db,
        workspace_id=workspace_id,
        settings=settings,
        cipher=CredentialCipher(settings.credential_encryption_key),
        transport=get_transport_key_pair(),
    )


SettingsService = Annotated[DeepSeekSettingsService, Depends(get_deepseek_settings_service)]


@router.get("/deepseek", response_model=DeepSeekConfigurationStatus)
def get_deepseek_status(service: SettingsService) -> DeepSeekConfigurationStatus:
    configured, masked_hint = service.configured()
    return DeepSeekConfigurationStatus(configured=configured, masked_hint=masked_hint)


@router.get("/deepseek/encryption-key", response_model=TransportPublicKeyRead)
def get_encryption_key(service: SettingsService) -> TransportPublicKeyRead:
    return TransportPublicKeyRead(public_key=service.public_key())


@router.put("/deepseek", response_model=DeepSeekSettingsResult)
def save_deepseek_api_key(payload: EncryptedAPIKeyWrite, service: SettingsService) -> DeepSeekSettingsResult:
    try:
        return service.save(payload.encrypted_api_key)
    except (SecretStorageError, SecretTransportError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/deepseek/test", response_model=DeepSeekConnectionResult)
async def test_deepseek_connection(
    payload: DeepSeekConnectionTestRequest,
    service: SettingsService,
) -> DeepSeekConnectionResult:
    return await service.test_connection(payload.encrypted_api_key)


@router.get("/site", response_model=SiteSettingsRead)
def get_site_settings(
    owner: OwnerWorkspace,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SiteSettingsRead:
    site = SiteSettingsService(db, settings).get()
    return SiteSettingsRead(public_access_enabled=site.public_access_enabled, updated_at=site.updated_at)


@router.put("/site", response_model=SiteSettingsRead)
def update_site_settings(
    payload: SiteSettingsUpdate,
    owner: OwnerWorkspace,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SiteSettingsRead:
    site = SiteSettingsService(db, settings).update(
        public_access_enabled=payload.public_access_enabled,
        updated_by=owner,
    )
    return SiteSettingsRead(public_access_enabled=site.public_access_enabled, updated_at=site.updated_at)
