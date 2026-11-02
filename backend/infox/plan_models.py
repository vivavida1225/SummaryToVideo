"""URL-free favorite selections and local replacement evidence, never save receipts."""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .asset_models import AuditModel, AssetItem, AssetReference


class PlanError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SelectionPolicy(AuditModel):
    search: Literal['반도체'] = '반도체'
    favorites_only: Literal[True] = True
    source: Literal['video'] = 'video'
    date_from: None = None
    reuse_policy: Literal['first_usable_reuse'] = 'first_usable_reuse'
    page_size: Literal[50] = 50
    max_pages: int = Field(default=20, ge=1, le=20)
    max_probes: int = Field(default=20, ge=1, le=100)


class FavoriteCandidate(AuditModel):
    source: Literal['video'] = 'video'
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    bucket: str
    segment: str
    thumbnail: str
    frame_index: int = Field(ge=0)
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    duration: float = Field(gt=0, allow_inf_nan=False)
    mime_type: Literal['video/mp4']
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    title: str = Field(default='', max_length=500)
    is_favorite: Literal[True] = True

    @field_validator('bucket', 'segment', 'thumbnail')
    @classmethod
    def stable_file(cls, value):
        return AssetReference.stable_reference(value)

    def key(self):
        data = [self.source, self.id, self.video_id, self.bucket, self.segment]
        return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


class SearchEvidence(AuditModel):
    source: Literal['video'] = 'video'
    search: Literal['반도체'] = '반도체'
    favorites_only: Literal[True] = True
    date_from: None = None
    fetched_at: str
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
    usable_schema_count: int = Field(ge=0)
    rejected_schema_count: int = Field(ge=0)


class ReplacementChoice(AuditModel):
    target: AssetItem
    candidate: FavoriteCandidate
    slot_frames: int = Field(gt=0)
    result_item_index: int | None = Field(default=None, ge=0)


class PreservationChecks(AuditModel):
    scope_preserved: bool = False
    normal_assets_preserved: bool = False
    layout_preserved: bool = False
    timing_preserved: bool = False
    references_consistent: bool = False
    old_active_references_removed: bool = False

    @property
    def ready(self):
        return all(self.model_dump().values())


class ReplacementPlan(AuditModel):
    state: Literal['not_started', 'selecting', 'selected', 'ready', 'not_needed', 'disabled', 'needs_attention'] = 'not_started'
    policy: SelectionPolicy = Field(default_factory=SelectionPolicy)
    source_version: str | None = None
    source_fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    result_fingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    selections: tuple[ReplacementChoice, ...] = ()
    search_evidence: SearchEvidence | None = None
    checks: PreservationChecks = Field(default_factory=PreservationChecks)
    checked_at: str | None = None
    error_code: Literal['audit_required', 'source_changed', 'draft_changed', 'candidates_empty',
        'no_usable_candidate', 'pagination_invalid', 'pagination_limit', 'availability_limit',
        'selection_unavailable', 'unsupported_uservideo', 'unsupported_timing', 'unsupported_media',
        'transform_invalid', 'authentication_required', 'read_failed'] | None = None

    @model_validator(mode='after')
    def ready_has_evidence(self):
        if self.state == 'ready' and (not self.selections or not self.source_version or not self.source_fingerprint
                or not self.result_fingerprint or not self.search_evidence or not self.checked_at
                or self.error_code or not self.checks.ready):
            raise ValueError('unverified replacement plan')
        if self.state == 'selected' and (not self.selections or not self.source_fingerprint or not self.source_version):
            raise ValueError('missing fixed selection')
        return self
