import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.enums import WorkspaceRole
from app.models.workspace import QuotaGuard, SiteSettings, Workspace


class SiteSettingsService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    def get(self) -> SiteSettings:
        current = self._db.get(SiteSettings, 1)
        if current is not None:
            return current
        self._lock_guard()
        current = self._db.get(SiteSettings, 1)
        if current is None:
            current = SiteSettings(
                id=1,
                public_access_enabled=self._settings.public_access_enabled,
            )
            self._db.add(current)
            self._db.commit()
            self._db.refresh(current)
        return current

    def update(self, *, public_access_enabled: bool, updated_by: Workspace) -> SiteSettings:
        if updated_by.role != WorkspaceRole.OWNER:
            raise PermissionError("仅站长可以修改公开访问状态")
        self._lock_guard()
        current = self._db.get(SiteSettings, 1)
        if current is None:
            current = SiteSettings(id=1, public_access_enabled=public_access_enabled)
            self._db.add(current)
        current.public_access_enabled = public_access_enabled
        current.updated_by_workspace_id = updated_by.id
        self._db.commit()
        self._db.refresh(current)
        return current

    def owner_workspace(self) -> Workspace:
        self._lock_guard()
        owner = self._db.scalar(select(Workspace).where(Workspace.owner_slot == 1).with_for_update())
        if owner is None:
            owner = Workspace(
                id=uuid.uuid4(),
                role=WorkspaceRole.OWNER,
                owner_slot=1,
            )
            self._db.add(owner)
            self._db.commit()
            self._db.refresh(owner)
        return owner

    def _lock_guard(self) -> QuotaGuard:
        guard = self._db.scalar(select(QuotaGuard).where(QuotaGuard.id == 1).with_for_update())
        if guard is None:
            guard = QuotaGuard(id=1)
            self._db.add(guard)
            self._db.flush()
        return guard
