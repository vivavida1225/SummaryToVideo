"""Errors shared by single-attempt provider transports and the retry runner."""

from typing import Literal


class ProviderError(Exception):
    def __init__(self, message: str, *, action: Literal['next_model', 'next_key', 'stop'] | None = None,
                 retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.action = action or ('next_model' if retryable else 'stop')
        self.retryable = self.action != 'stop'
        self.retry_after = retry_after
