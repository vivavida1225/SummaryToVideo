from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from ..run_models import StrictModel, Receipt

PublishStage = Literal['prepared', 'preflighting', 'starting_render', 'rendering', 'generating_metadata',
    'saving_metadata', 'starting_upload', 'uploading', 'copying_link', 'completed', 'failed',
    'needs_attention', 'clipboard_failed']
TERMINAL = {'completed', 'failed', 'needs_attention', 'clipboard_failed'}
Operation = Literal['render', 'metadata_generate', 'metadata_update', 'upload']
ErrorCode = Literal['preflight_unconfirmed', 'existing_publication', 'remote_changed', 'stream_unavailable',
    'stream_overflow', 'stream_unrecognized', 'remote_task_failed', 'history_unrecognized',
    'stage_timeout', 'acceptance_unknown', 'authentication_required', 'internal_error',
    'server_restarted', 'clipboard_error', 'invalid_share_url', 'metadata_mismatch',
    'publication_active', 'previous_unconfirmed']


class PublishInput(StrictModel):
    intent_id: str
    market_date: str
    review_confirmed: bool = False
    reviewed_remote_updated_at: str | None = None

    @field_validator('reviewed_remote_updated_at')
    @classmethod
    def saved_version(cls, value):
        from ..save_models import remote_version
        return remote_version(value) if value is not None else None

    @field_validator('intent_id')
    @classmethod
    def uuid_string(cls, value):
        return UUID(value).hex

    @field_validator('market_date')
    @classmethod
    def calendar_day(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('invalid date')
        return value


class RerenderInput(PublishInput):
    previous_publication_id: str = Field(pattern=r'^[0-9a-f]{32}$')


def format_publish_metadata(market_date: date) -> tuple[str, str]:
    return (f'{market_date.year % 100:02d}{market_date.month:02d}{market_date.day:02d} AI 영상 시황',
            f'{market_date.year}년 {market_date.month}월 {market_date.day}일 AI 영상 시황')


class PublishSnapshot(StrictModel):
    market_date: str
    title: str
    description: str
    privacy_status: Literal['unlisted'] = 'unlisted'
    remote_updated_at: str | None = None
    baseline_remote_task_ids: tuple[str, ...] = ()
    review_confirmed_at: str | None = None
    reviewed_remote_updated_at: str | None = None
    review_warning_codes: tuple[str, ...] = ()


class PublishReceipt(Receipt):
    response_verified: bool = False
    read_verified: bool = False


class PublishReceipts(StrictModel):
    render: PublishReceipt = Field(default_factory=PublishReceipt)
    metadata_generate: PublishReceipt = Field(default_factory=PublishReceipt)
    metadata_update: PublishReceipt = Field(default_factory=PublishReceipt)
    upload: PublishReceipt = Field(default_factory=PublishReceipt)


class PublicationRecord(StrictModel):
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    run_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    attempt_number: int = Field(default=1, ge=1)
    previous_publication_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    version: int = Field(default=0, ge=0)
    stage: PublishStage = 'prepared'
    failed_stage: PublishStage | None = None
    error_code: ErrorCode | None = None
    created_at: str
    updated_at: str
    snapshot: PublishSnapshot
    render_task_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    metadata_task_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    upload_task_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    progress: int | None = Field(default=None, ge=0, le=100)
    receipts: PublishReceipts = Field(default_factory=PublishReceipts)
    render_file_fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    keywords: tuple[str, ...] = ()
    youtube_video_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{11}$')
    share_url: str | None = None
    uploaded_verified: bool = False
    clipboard_state: Literal['not_started', 'intent_recorded', 'copied', 'failed', 'unknown'] = 'not_started'


class PublishEvent(StrictModel):
    task_id: str
    status: Literal['initializing', 'running', 'completed', 'failed', 'unrecognized']
    progress: int | None = None
    error_present: bool = False


class StageVerification(StrictModel):
    status: Literal['waiting', 'completed', 'failed', 'unknown']
    error_code: ErrorCode | None = None
    evidence: dict = Field(default_factory=dict)


class PublishTimeouts(StrictModel):
    connect: float = Field(default=15, gt=0)
    read: float = Field(default=30, gt=0)
    preflight: float = Field(default=60, gt=0)
    render: float = Field(default=1200, gt=0)
    metadata: float = Field(default=300, gt=0)
    save: float = Field(default=60, gt=0)
    upload: float = Field(default=600, gt=0)
