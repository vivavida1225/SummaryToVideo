"""Bounded favorites-only collection. Signed URLs live only in the ephemeral page."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from pydantic import ValidationError

from .plan_models import FavoriteCandidate, PlanError, SearchEvidence, SelectionPolicy


def candidate_from_item(raw):
    title = raw.get('title', '')
    if not isinstance(title, str) or '://' in title or any(ord(c) < 32 for c in title):
        title = ''  # A display label, never a candidate eligibility filter.
    return FavoriteCandidate(id=raw['id'], video_id=raw['videoId'], bucket=raw['bucket'],
        segment=raw['segment'], thumbnail=raw['thumbnail'], frame_index=raw['frameIndex'],
        timestamp=raw['timestamp'], duration=raw['duration'], mime_type=raw['mime_type'],
        width=raw['resolution_width'], height=raw['resolution_height'], title=title[:500],
        is_favorite=raw['is_favorite'])


@dataclass
class FavoriteResults:
    candidates: tuple[FavoriteCandidate, ...]
    raw: dict
    evidence: SearchEvidence


async def collect_favorites(client, *, policy=None, source='video'):
    if source != 'video':
        raise PlanError('unsupported_uservideo')
    policy = policy or SelectionPolicy()
    skip, total, rejected = 0, None, 0
    candidates, raw_items, seen_ids = [], {}, set()
    for page in range(policy.max_pages):
        result = await client.search_favorites(source='video', search=policy.search, skip=skip, limit=policy.page_size)
        items = result.get('items')
        if (not isinstance(items, list) or type(result.get('total')) is not int or result['total'] < 0
                or result.get('skip') != skip or result.get('limit') != policy.page_size
                or len(items) > policy.page_size or skip + len(items) > result['total']):
            raise PlanError('pagination_invalid')
        if total is not None and total != result['total']:
            raise PlanError('pagination_invalid')
        total = result['total']
        for raw in items:
            if not isinstance(raw, dict) or raw.get('is_favorite') is not True:
                raise PlanError('pagination_invalid')
            item_id = raw.get('id')
            if not isinstance(item_id, str) or item_id in seen_ids:
                raise PlanError('pagination_invalid')
            seen_ids.add(item_id)
            try:
                candidate = candidate_from_item(raw)
                if math.floor(candidate.duration*30)<1:
                    raise ValueError('subframe clip')
            except (KeyError, TypeError, ValueError, ValidationError):
                rejected += 1
                continue
            candidates.append(candidate)
            raw_items[candidate.key()] = raw
        skip += len(items)
        if skip == total:
            return FavoriteResults(tuple(candidates), raw_items, SearchEvidence(
                fetched_at=datetime.now(timezone.utc).isoformat(), total=total, pages=page+1,
                usable_schema_count=len(candidates), rejected_schema_count=rejected))
        if not items:
            raise PlanError('pagination_invalid')
    raise PlanError('pagination_limit')


async def choose_favorite(client, results, *, frozen=None, policy=None):
    policy = policy or SelectionPolicy()
    if frozen is not None:
        found = next((candidate for candidate in results.candidates if candidate == frozen), None)
        if found is None or not await client.favorite_segment_available(results.raw[frozen.key()]):
            raise PlanError('selection_unavailable')
        return frozen
    if not results.evidence.total:
        raise PlanError('candidates_empty')
    for index, candidate in enumerate(results.candidates):
        if index >= policy.max_probes:
            raise PlanError('availability_limit')
        if await client.favorite_segment_available(results.raw[candidate.key()]):
            return candidate
    raise PlanError('no_usable_candidate')
