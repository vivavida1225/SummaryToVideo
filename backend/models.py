"""Ordered text model catalog shared by session discovery and job execution.

Gemini 3.7 Flash routes to 3.8 Flash and is accepted only for compatibility.
Source: https://ai.google.dev/gemini-api/docs/deprecations (2026-10-14)
"""

MODELS = (
    {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash'},
    {'id': 'gemini-3.6-flash', 'label': 'Gemini 3.6 Flash'},
    {'id': 'gemini-3.5-flash-lite', 'label': 'Gemini 3.5 Flash-Lite'},
)
MODEL = MODELS[0]['id']


def model_candidates(model: str) -> tuple[str, ...]:
    if model == 'gemini-3.7-flash':
        model = 'gemini-3.8-flash'
    ids = tuple(item['id'] for item in MODELS)
    if model not in ids:
        raise ValueError('지원하지 않는 모델입니다. 모델 목록에서 다시 선택하세요.')
    return ids[ids.index(model):]


def model_label(model: str) -> str:
    return next((item['label'] for item in MODELS if item['id'] == model), model)
