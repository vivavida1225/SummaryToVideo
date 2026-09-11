"""Strict shape/length/index preservation checks, without rewriting facts."""

import re
from dataclasses import dataclass


class ValidationError(ValueError):
    pass


@dataclass
class ValidatedText:
    text: str
    body_char_count: int
    warnings: list[str]


def index_triples(serialized: str) -> list[str]:
    first = serialized.split('\n===\n', 1)[0].splitlines()
    output = []
    for name in ('코스피', '코스닥'):
        positions = [i for i, line in enumerate(first) if line == name]
        if len(positions) != 1 or positions[0] + 2 >= len(first):
            raise ValidationError(f'입력에 {name} 최종 지수와 등락폭·등락률이 필요합니다.')
        i = positions[0]
        if not re.fullmatch(r'[\d,]+(?:\.\d+)?', first[i + 1]) or not re.fullmatch(
            r'[+\-−]?[\d,]+(?:\.\d+)?\s*\([+\-−]?[\d,]+(?:\.\d+)?%\)', first[i + 2]
        ):
            raise ValidationError(f'입력의 {name} 지수/등락 표기를 확인하세요.')
        output.extend(first[i:i + 3])
    return output


def validate_compressed(response: str, serialized: str) -> ValidatedText:
    response = response.replace('\r\n', '\n').strip()
    fence = re.fullmatch(r'```text\n(.+)\n```', response, flags=re.DOTALL)
    if not fence or '`' in fence[1]:
        raise ValidationError('응답은 하나의 text 코드블록이어야 하며 바깥 설명은 허용되지 않습니다.')
    body = fence[1]
    scenes = body.split('\n===\n')
    if len(scenes) != 5:
        raise ValidationError('정확히 5개 장면과 4개의 === 구분자가 필요합니다.')
    expected_indices = index_triples(serialized)
    warnings = []
    chars = 0
    for i, scene in enumerate(scenes, 1):
        lines = scene.split('\n')
        heading = re.fullmatch(rf'<{i}>([^<>]+)', lines[0])
        if not heading or heading[1] != heading[1].strip():
            raise ValidationError(f'장면 {i}: <{i}> 바로 뒤에 제목이 필요합니다.')
        if i == 1:
            if len(lines) != 9 or lines[2] != '' or lines[3:] != expected_indices:
                raise ValidationError('장면 1: 설명 한 줄, 빈 줄 하나, 원문 그대로의 코스피·코스닥 지수 6줄이 필요합니다.')
            if not lines[1].startswith('· '):
                raise ValidationError('장면 1 설명은 · 와 공백으로 시작해야 합니다.')
            description = lines[1][2:]
        else:
            if len(lines) != 2:
                raise ValidationError(f'장면 {i}: 제목과 설명 두 줄만 허용됩니다.')
            description = lines[1]
        if not description.strip() or description != description.strip():
            raise ValidationError(f'장면 {i}: 설명이 비어 있거나 바깥쪽 공백이 있습니다.')
        limit = 45 if i == 1 else 70
        if len(description) > limit:
            raise ValidationError(f'장면 {i}: 설명은 {limit}자 이내여야 합니다 (현재 {len(description)}자).')
        if re.search(r'[<>]|#{1,}|\*\*|\|', heading[1] + description):
            raise ValidationError(f'장면 {i}: HTML/Markdown 장식을 제거하세요.')
        chars += len(heading[1]) + len(lines[1])
        if len(heading[1]) > 25:
            warnings.append(f'장면 {i} 제목이 권장 길이 25자를 초과합니다.')
    if chars > 650:
        raise ValidationError(f'전체 본문이 650자를 초과합니다 (현재 {chars}자).')
    if not 400 <= chars <= 550:
        warnings.append(f'본문 {chars}자: 권장 분량은 400~550자입니다.')
    return ValidatedText(body, chars, warnings)
