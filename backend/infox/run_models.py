"""Validated, credential-free durable execution records for InfoX."""

import hashlib
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunError(Exception):
    def __init__(self, code: str, status_code: int = 409, run_id: str | None = None):
        self.code, self.status_code, self.run_id = code, status_code, run_id
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, validate_default=True)


class ProductionSettings(StrictModel):
    title: str = Field(default='시황 영상', min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    template_id: int = Field(default=1786, gt=0)
    voice_id: str = Field(default='e9d18b28-937c-42ae-8fe0-de92517c233f', pattern=r'^[A-Za-z0-9_-]{1,128}$')
    voice_provider: Literal['elevenlabs'] = 'elevenlabs'
    voice_speed: Literal['1.0'] = '1.0'
    target_duration: Literal[60] = 60
    asset_mode: Literal['ai_recommend'] = 'ai_recommend'
    video_matching: bool = True
    ai_image_style: Literal['photo'] = 'photo'
    ai_image_aspect_ratio: Literal['4:3'] = '4:3'
    lang: Literal['ko'] = 'ko'
    script_version: Literal['v1'] = 'v1'
    auto_replace: bool = True
    replacement_search: Literal['반도체'] = '반도체'

    @field_validator('title')
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError('empty title')
        return value


class PrepareRunInput(StrictModel):
    intent_id: str
    revision: int = Field(ge=0)
    settings: ProductionSettings = Field(default_factory=ProductionSettings)
    review_confirmed: bool = False
    new_run: bool = False

    @field_validator('intent_id')
    @classmethod
    def valid_intent(cls, value):
        return UUID(value).hex


class Scene(StrictModel):
    line_number: int = Field(ge=1)
    scene_index: int = Field(ge=0)
    text: str


class Snapshot(StrictModel):
    job_id: str = Field(pattern=r'^\d{8}_\d{6}_\d{6}_[0-9a-f]{8}$')
    revision: int = Field(ge=0)
    text: str
    text_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    scenes: tuple[Scene, ...]
    settings: ProductionSettings
    confirmed_at: str
    review_confirmed: bool
    generation_state: Literal['completed', 'needs_review', 'failed']
    validation_issues: tuple[str, ...]
    warnings: tuple[str, ...]


class Receipt(StrictModel):
    state: Literal['not_sent', 'intent_recorded', 'accepted', 'unknown', 'rejected'] = 'not_sent'
    intent_at: str | None = None
    acknowledged_at: str | None = None


RunStage = Literal['prepared', 'creating_project', 'project_created', 'starting_generation', 'generating',
    'checking_draft', 'auditing_assets', 'selecting_favorites', 'replacing_assets', 'saving_assets',
    'verifying_assets', 'ready_for_editor', 'needs_attention', 'failed']


class RunRecord(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    stage: RunStage = 'prepared'
    version: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str
    snapshot: Snapshot
    project: Receipt = Field(default_factory=Receipt)
    generation: Receipt = Field(default_factory=Receipt)
    video_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')
    task_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$')

    @property
    def requires_reconciliation(self) -> bool:
        return any(r.state in ('intent_recorded', 'unknown') for r in (self.project, self.generation))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def public_record(record: RunRecord) -> dict:
    return {**record.model_dump(mode='json'), 'requires_reconciliation': record.requires_reconciliation}
