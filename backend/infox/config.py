"""Use the existing Git-excluded backend .env; do not persist session tokens."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from ..config import ROOT


@dataclass(frozen=True)
class InfoXCredentials:
    email: str = field(default='', repr=False)
    password: str = field(default='', repr=False)
    access_token: str = field(default='', repr=False)
    device_token: str = field(default='', repr=False)

    @classmethod
    def load(cls, root: Path = ROOT) -> 'InfoXCredentials':
        values = dotenv_values(root / '.env', encoding='utf-8-sig', interpolate=False)

        def get(name: str) -> str:
            return os.environ.get(name, values.get(name) or '')

        return cls(email=get('INFOX_USERNAME').strip(), password=get('INFOX_PASSWORD'),
                   access_token=get('INFOX_ACCESS_TOKEN').strip(),
                   device_token=get('INFOX_ORG_DEVICE_TOKEN').strip())
