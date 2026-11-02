"""Strict draft readiness and task-scoped events. Never persist remote payloads."""

import math
from typing import Literal

from .run_models import DraftCheck, RunRecord, StrictModel


class TaskEvent(StrictModel):
    status: Literal['running', 'completed', 'failed', 'unrecognized']
    progress: int | None = None


def task_event(body: dict, video_id: str, task_id: str) -> TaskEvent | None:
    if not isinstance(body, dict):
        return None
    if 'channel' in body and body['channel'] != f'infox:{video_id}':
        return None
    for key in ('video_id', 'videoid'):
        if key in body and str(body[key]) != video_id:
            return None
    kwargs = body.get('additional_kwargs')
    if isinstance(kwargs, dict) and kwargs.get('taskid') == task_id:
        status = kwargs.get('task_status')
        failed = bool(kwargs.get('error')) or status in ('failed', 'error', 'cancelled', 'canceled', 'revoked')
        if status == 'processing':
            status = 'running'
        status = 'failed' if failed else status if status in ('completed', 'running') else 'unrecognized'
        progress = kwargs.get('progress')
        if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 100:
            progress = None
        return TaskEvent(status=status, progress=int(progress) if progress is not None else None)
    # Known message envelopes; don't recursively inspect arbitrary video metadata.
    matched = []
    for key in ('messages', 'message', 'data'):
        nested = body.get(key)
        for item in nested if isinstance(nested, list) else [nested]:
            if isinstance(item, dict) and (event := task_event(item, video_id, task_id)):
                matched.append(event)
    # A batch can contain several revisions of the same task. Never hide a
    # terminal signal behind an earlier progress item; errors take precedence.
    priority = {'unrecognized': 0, 'running': 1, 'completed': 2, 'failed': 3}
    return max(matched, key=lambda event: priority[event.status]) if matched else None


def _positive(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def verify_draft(record: RunRecord, state: dict) -> DraftCheck:
    scenes = state.get('video', {}).get('scenes') if isinstance(state.get('video'), dict) else None
    matching = isinstance(scenes, list) and len(scenes) == len(record.snapshot.scenes)
    voices, captions = bool(matching), bool(matching)
    settings = record.snapshot.settings
    if matching:
        for expected, scene in zip(record.snapshot.scenes, scenes):
            voice = scene.get('voiceover') if isinstance(scene, dict) else None
            if not isinstance(voice, dict):
                matching = voices = captions = False
                continue
            matching = matching and voice.get('content') == expected.text
            voices = voices and all(isinstance(voice.get(k), str) and voice[k].strip() for k in ('filename', 'bucket'))
            voices = bool(voices and _positive(voice.get('audio_duration')) and
                voice.get('voice_id') == settings.voice_id and voice.get('voice_provider') == settings.voice_provider
                and voice.get('voice_speed') == settings.voice_speed)
            segments = voice.get('segment_captions')
            valid = isinstance(segments, list) and bool(segments)
            previous_end = 0
            text = ''
            if valid:
                for segment in segments:
                    if not isinstance(segment, dict):
                        valid = False
                        break
                    start, end = segment.get('startMs'), segment.get('endMs')
                    if (type(start) not in (int, float) or type(end) not in (int, float)
                        or not math.isfinite(start) or not math.isfinite(end)
                        or start < previous_end or end <= start or not isinstance(segment.get('text'), str)):
                        valid = False
                        break
                    previous_end = end
                    text += segment['text']
            captions = bool(captions and valid and ''.join(text.split()) == ''.join(expected.text.split()))
    return DraftCheck(identity_matches=bool(record.video_id and str(state.get('id')) == record.video_id),
                      scenes_match=bool(matching), voices_ready=bool(voices), captions_ready=bool(captions))
