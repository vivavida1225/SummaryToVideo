import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from .models import MODEL


ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 5 * 1024 * 1024


@dataclass
class Settings:
    root: Path = ROOT
    model: str = MODEL

    @property
    def prompt_path(self) -> Path:
        return self.root / 'prompts/compress_to_1min.txt'

    def keys(self, provider: str = 'gemini') -> list[tuple[int, str]]:
        values = dotenv_values(self.root / '.env', encoding='utf-8-sig')
        if provider == 'openai':
            value = os.environ.get('OPENAI_API_KEY', '').strip() or (values.get('OPENAI_API_KEY') or '').strip()
            return [(1, value)] if value else []
        pairs = []
        for name, value in values.items():
            match = re.fullmatch(r'GEMINI_API_KEY_([1-9]\d*)', name)
            if match and value and value.strip():
                pairs.append((int(match[1]), value.strip()))
        return sorted(pairs)

    def redact(self, message: str) -> str:
        values = dotenv_values(self.root / '.env', encoding='utf-8-sig')
        secrets = [value for provider in ('gemini', 'openai') for _, value in self.keys(provider)]
        file_key = (values.get('OPENAI_API_KEY') or '').strip()
        if file_key:
            secrets.append(file_key)
        for value in sorted(set(secrets), key=len, reverse=True):
            message = message.replace(value, '[API KEY]')
        return message
