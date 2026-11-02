"""Credential-free, immutable asset inspection and verified provenance records."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuditModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, validate_default=True)


class AssetPolicy(AuditModel):
    aliases: tuple[str, ...] = ('삼성전자', 'SK하이닉스', '삼성', 'SK', '하이닉스', '삼전닉스')
    supported_templates: tuple[int, ...] = (1786,)
    max_nodes: int = Field(default=10000, ge=1, le=10000)

    @field_validator('aliases')
    @classmethod
    def aliases_valid(cls, values):
        if not values or len(values) > 32 or any(not x.strip() or len(x) > 64 for x in values):
            raise ValueError('invalid alias policy')
        return values


class AssetReference(AuditModel):
    kind: Literal['image', 'video']
    bucket: str | None = None
    filename: str | None = None
    segment: str | None = None
    video_id: str | None = None
    frame_index: int | None = Field(default=None, ge=0)
    timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, ge=0)

    @field_validator('bucket', 'filename', 'segment', 'video_id')
    @classmethod
    def stable_reference(cls, value):
        if value is not None and (not value.strip() or len(value) > 512 or
                any(ord(c) < 32 for c in value) or any(c in value for c in ('?', '#', '://'))):
            raise ValueError('invalid stable reference')
        return value


class AssetHit(AuditModel):
    field: str
    term: str
    source: str


class AssetItem(AuditModel):
    scene_index: int = Field(ge=0)
    scene_id: str | None
    layer_id: str
    item_index: int | None = Field(default=None, ge=0)
    reference: AssetReference
    verdict: Literal['detected', 'not_detected', 'unknown', 'favorite_exempt']
    hits: tuple[AssetHit, ...] = ()
    metadata_sources: tuple[str, ...] = ()
    reason: Literal['metadata_inspected', 'company_detected', 'metadata_missing',
        'reference_incomplete', 'verified_favorite_replacement', 'ambiguous_media', 'metadata_unreadable']


class AssetIssue(AuditModel):
    scene_index: int | None = None
    layer_id: str | None = None
    code: Literal['unsupported_template', 'invalid_video', 'invalid_scenes', 'scene_id_missing',
        'invalid_graph', 'missing_layer', 'cycle_or_shared_layer', 'invalid_marker', 'unsupported_layer',
        'invalid_media', 'invalid_bundle', 'inspection_limit', 'no_inspectable_assets', 'version_missing', 'metadata_unreadable']


class FavoriteReplacement(AuditModel):
    """Only a future verified save may create this; never accept remote favorite flags."""
    run_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    scene_index: int = Field(ge=0)
    scene_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    layer_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    item_index: int | None = Field(default=None, ge=0)
    reference: AssetReference
    source: Literal['video', 'uservideo']
    search: Literal['반도체']
    favorites_only: Literal[True]
    saved_verified: Literal[True]
    verified_at: str

    @field_validator('verified_at')
    @classmethod
    def iso_time(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        return value


class AssetAudit(AuditModel):
    state: Literal['not_started', 'running', 'completed', 'needs_attention'] = 'not_started'
    audited_at: str | None = None
    source_version: str | None = None
    policy: AssetPolicy = Field(default_factory=AssetPolicy)
    items: tuple[AssetItem, ...] = ()
    issues: tuple[AssetIssue, ...] = ()
    error_code: Literal['authentication_required', 'read_failed', 'draft_changed', 'inspection_incomplete'] | None = None

    @model_validator(mode='after')
    def complete_has_evidence(self):
        if self.state == 'completed' and (not self.audited_at or not self.source_version or not self.items
                or self.issues or self.error_code or any(item.verdict == 'unknown' for item in self.items)):
            raise ValueError('incomplete inspection')
        return self

    def counts(self) -> dict[str, int]:
        return {key: sum(item.verdict == key for item in self.items)
                for key in ('detected', 'not_detected', 'unknown', 'favorite_exempt')}
