import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from .compression import MODEL


ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 5 * 1024 * 1024


@dataclass
class Settings:
    root: Path = ROOT
    model: str = MODEL

    @property
    def prompt_path(self) -> Path:
        return self.root / 'prompts/compress_to_1min.txt'

    def keys(self) -> list[tuple[int, str]]:
        values = dotenv_values(self.root / '.env', encoding='utf-8-sig')
        pairs = []
        for name, value in values.items():
            match = re.fullmatch(r'GEMINI_API_KEY_([1-9]\d*)', name)
            if match and value and value.strip():
                pairs.append((int(match[1]), value.strip()))
        return sorted(pairs)

    def redact(self, message: str) -> str:
        for _, value in self.keys():
            message = message.replace(value, '[API KEY]')
        return message
