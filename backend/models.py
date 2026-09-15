"""Ordered model catalog shared by session discovery and job execution."""

MODELS = (
    {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash'},
    {'id': 'gemini-3.7-flash', 'label': 'Gemini 3.7 Flash'},
    {'id': 'gemini-3.6-flash', 'label': 'Gemini 3.6 Flash'},
    {'id': 'gemini-3.5-flash-lite', 'label': 'Gemini 3.5 Flash-Lite'},
)
MODEL = MODELS[0]['id']


def model_candidates(model: str) -> tuple[str, ...]:
    ids = tuple(item['id'] for item in MODELS)
    if model not in ids:
        raise ValueError('지원하지 않는 모델입니다. 모델 목록에서 다시 선택하세요.')
    return ids[ids.index(model):]


def model_label(model: str) -> str:
    return next((item['label'] for item in MODELS if item['id'] == model), model)
