"""Credential-free save evidence. Remote documents and signed URLs stay in memory."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .asset_models import AuditModel


def remote_version(value: str) -> str:
    if not isinstance(value, str) or not 10 <= len(value) <= 64:
        raise ValueError('invalid remote version')
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return value  # Preserve the exact server value in updatedAt.


class AssetSave(AuditModel):
    state: Literal['not_started', 'checking_source', 'saving', 'verifying',
                   'saved_verified', 'unchanged_verified', 'needs_attention'] = 'not_started'
    receipt_state: Literal['not_sent', 'intent_recorded', 'accepted', 'unknown', 'rejected'] = 'not_sent'
    intent_at: str | None = None
    request_version: str | None = None
    result_url_fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    response_version: str | None = None
    verified_version: str | None = None
    verified_fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    checked_at: str | None = None
    write_error_code: Literal['version_conflict', 'authentication_required', 'save_rejected', 'save_unconfirmed'] | None = None
    write_status: int | None = Field(default=None, ge=100, le=599)
    read_error_code: Literal['authentication_required', 'read_failed', 'verification_failed'] | None = None
    error_code: Literal['source_changed', 'draft_changed', 'selection_unavailable', 'context_invalid',
        'transform_invalid', 'version_conflict', 'save_rejected', 'save_unconfirmed',
        'verification_failed', 'authentication_required', 'read_failed'] | None = None

    @field_validator('request_version', 'response_version', 'verified_version')
    @classmethod
    def version_valid(cls, value):
        return remote_version(value) if value is not None else None

    @model_validator(mode='after')
    def evidence_required(self):
        if self.receipt_state != 'not_sent' and (not self.intent_at or not self.request_version or not self.result_url_fingerprint):
            raise ValueError('missing save intent')
        if self.receipt_state == 'accepted' and not self.response_version:
            raise ValueError('missing save response version')
        if self.state in ('saved_verified', 'unchanged_verified'):
            if not self.verified_version or not self.verified_fingerprint or not self.checked_at or self.error_code:
                raise ValueError('missing verification evidence')
            if self.state == 'saved_verified' and self.receipt_state not in ('accepted', 'unknown', 'intent_recorded', 'rejected'):
                raise ValueError('save not attempted')
            if self.state == 'unchanged_verified' and self.receipt_state != 'not_sent':
                raise ValueError('unexpected save')
        return self
