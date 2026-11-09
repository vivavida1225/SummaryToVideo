from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from ..run_models import StrictModel

ACTIVE = {'pending', 'running'}


class RetryInput(StrictModel):
    intent_id: str

    @field_validator('intent_id')
    @classmethod
    def valid_intent(cls, value):
        return UUID(value).hex


class DownloadInput(RetryInput):
    kind: Literal['video', 'all']


class DownloadItem(StrictModel):
    kind: Literal['script', 'source', 'video']
    filename: str
    state: Literal['pending', 'downloading', 'completed', 'failed'] = 'pending'
    path: str | None = None
    size: int | None = None
    sha256: str | None = None
    error_code: str | None = None


class DownloadRecord(StrictModel):
    id: str
    publication_id: str
    kind: Literal['video', 'all']
    automatic: bool = False
    state: Literal['pending', 'running', 'completed', 'partial', 'failed', 'needs_attention'] = 'pending'
    created_at: str
    updated_at: str
    items: tuple[DownloadItem, ...]
    error_code: str | None = None
    version: int = Field(default=0, ge=0)
