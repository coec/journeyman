"""System-wide approval workflow controls."""

from app import db
from app.models import DirectorySetting, SystemSetting
from app.models.directory import DIRECTORY_SETTING_ID
from app.models.system_setting import SYSTEM_SETTING_ID


class ApprovalWorkflowSettingsError(ValueError):
    """Invalid system-wide approval workflow change."""


def four_eyes_enabled():
    """Return whether Project review/management approval is enabled."""

    settings = db.session.get(SystemSetting, SYSTEM_SETTING_ID)
    return bool(settings is not None and settings.four_eyes_enabled)


def directory_authentication_is_enabled():
    """Return whether LDAP/AD authentication is enabled."""

    settings = db.session.get(DirectorySetting, DIRECTORY_SETTING_ID)
    return bool(settings is not None and settings.enabled)


def set_four_eyes_enabled(enabled, *, updated_by):
    """Enable or suspend the approval workflow without deleting history."""

    from app.services.system_settings import get_or_create_system_settings

    enabled = bool(enabled)
    settings = get_or_create_system_settings()

    if enabled and not directory_authentication_is_enabled():
        raise ApprovalWorkflowSettingsError(
            "4-eyes approval cannot be enabled until Directory and Authentication is enabled."
        )

    previous = bool(settings.four_eyes_enabled)
    settings.four_eyes_enabled = enabled
    settings.updated_by = str(updated_by or "system").strip() or "system"
    db.session.commit()
    return settings, previous
