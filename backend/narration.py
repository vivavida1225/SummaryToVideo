"""Decode explicit scene boundaries without guessing or rewriting narration."""

from .validation import ValidationError, count_characters, validate_compressed

SCENE_BREAK = '<SCENE_BREAK>'


def decode_response(raw: str) -> tuple[str, list[str]]:
    if SCENE_BREAK not in raw:
        return raw, []  # Existing five-line responses remain supported.
    scenes = [scene.strip() for scene in raw.split(SCENE_BREAK)]
    issues = []
    if len(scenes) != 5:
        issues.append(f'{SCENE_BREAK} 구분자는 정확히 4개여야 합니다. 현재 {len(scenes) - 1}개입니다.')
    if any(not scene for scene in scenes):
        issues.append('구분자 사이에 빈 장면이 있습니다. 각 장면에 본문을 작성하세요.')
    if any('\n' in scene or '\r' in scene for scene in scenes):
        issues.append('장면 내부의 줄바꿈을 제거하고 장면 경계에만 구분자를 사용하세요.')
    return '\n'.join(scenes), issues


def validate_response(raw: str, serialized: str):
    text, issues = decode_response(raw)
    try:
        result = validate_compressed(text, serialized)
    except ValidationError as exc:
        issues.extend(exc.issues)
    if issues:
        raise ValidationError(issues, body_char_count=count_characters(text))
    return result
