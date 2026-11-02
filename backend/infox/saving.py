"""Whole-plan one-shot save and read-only reconciliation. No remote payload logging."""

import copy
import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from .assets import audit_assets
from .errors import InfoXError
from .favorites import collect_favorites, choose_favorite
from .plan_models import PlanError
from .replacement import apply_replacements, make_choices, state_fingerprint, validate_replacements, _ephemeral
from .run_models import RunError
from .save_models import remote_version
from .tracking import verify_draft


def url_reference_fingerprint(state):
    """Keep every signed URL's location/path; discard only its expiring query.

    Store only the hash. This includes normal assets and preview references, so
    a signature refresh cannot hide a changed file, domain or added URL field.
    """
    entries = []
    def visit(value, path):
        if isinstance(value, dict):
            for key in sorted(value):
                item = value[key]
                if key in ('preview_presigned', 'segment_url_presigned', 'url_presigned'):
                    if isinstance(item, str):
                        parts = urlsplit(item)
                        item = urlunsplit((parts.scheme, parts.netloc, parts.path, '', parts.fragment))
                    entries.append((path + [key], item))
                else:
                    visit(item, path + [key])
        elif isinstance(value, list):
            for index, item in enumerate(value): visit(item, path + [index])
    try:
        visit(state, [])
        return hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise PlanError('transform_invalid') from None


def _verify_candidate_urls(state, choices):
    for choice in choices:
        scene = state['video']['scenes'][choice.target.scene_index]
        media = scene['rootShape']['layers'][choice.target.layer_id]['props']['media']
        actual = media['image_files'][choice.result_item_index] if choice.result_item_index is not None else media
        _ephemeral(choice.candidate, {**actual, 'is_favorite': True})


def save_body(state):
    """Verified editor mapping: fetchVideoState -> videoState.context -> save hook.

    Missing context is unsupported; explicit null is preserved as observed by the
    service API. Never use creation context or a synthetic empty dict here.
    """
    try:
        version = remote_version(state['updated_at'])
        if not isinstance(state['video'], dict) or (state['context'] is not None and not isinstance(state['context'], dict)):
            raise ValueError('invalid context')
        return {'video': copy.deepcopy(state['video']), 'contextData': copy.deepcopy(state['context']), 'updatedAt': version}
    except (KeyError, TypeError, ValueError):
        raise PlanError('context_invalid') from None


def _error_code(error):
    if isinstance(error, PlanError):
        return error.code if error.code in ('source_changed', 'draft_changed', 'selection_unavailable',
                                            'context_invalid', 'transform_invalid') else 'selection_unavailable'
    if isinstance(error, InfoXError) and error.code in ('auth_expired', 'permission_denied', 'configuration_error'):
        return 'authentication_required'
    return 'read_failed'


def _record_outcome(store, claimed, outcome, response_version=None):
    # Only retry local CAS, tied to this exact intent. Never repeat the HTTP.
    current = claimed
    for _ in range(3):
        if current.asset_save.intent_at != claimed.asset_save.intent_at:
            raise RunError('record_conflict')
        if current.asset_save.state == 'saved_verified' or current.asset_save.receipt_state == 'accepted':
            return current
        try:
            return store.record_save_outcome(current.id, outcome, response_version=response_version,
                                             expected_version=current.version)
        except RunError as error:
            if error.code != 'record_conflict':
                raise
            current = store.get(current.id)
    raise RunError('record_conflict')


async def _verify_saved(store, client, r, failure=None):
    """Also handles crash/unknown/rejected: only inspect, never replay a POST."""
    state = await client.video_state(r.video_id)
    try:
        version = remote_version(state.get('updated_at'))
        # Plan hashes include the source's exact timestamp spelling. Ignore only
        # the known server-owned version field, not unrelated JSON mutations.
        content = {**state, 'updated_at': r.asset_save.request_version}
        fingerprint = state_fingerprint(content)
        url_fingerprint = url_reference_fingerprint(state)
        _verify_candidate_urls(state, r.replacement_plan.selections)
        matches = (fingerprint == r.replacement_plan.result_fingerprint and verify_draft(r, state).ready
                   and url_fingerprint == r.asset_save.result_url_fingerprint
                   and datetime.fromisoformat(version) > datetime.fromisoformat(r.asset_save.request_version)
                   and (r.asset_save.response_version is None or version == r.asset_save.response_version))
    except (ValueError, TypeError, KeyError, IndexError, PlanError):
        matches = False
    if matches:
        return store.finish_save(r.id, verified_version=version, fingerprint=fingerprint,
                                 url_fingerprint=url_fingerprint, expected_version=r.version)
    code = failure or ('verification_failed' if r.asset_save.receipt_state == 'accepted'
                       else 'save_rejected' if r.asset_save.receipt_state == 'rejected' else 'save_unconfirmed')
    return store.fail_save(r.id, code, expected_version=r.version)


async def save_plan(store, client, r):
    """r is durably claimed with begin_save; one whole-plan save per run."""
    try:
        await client.authenticate()
        if r.asset_save.receipt_state != 'not_sent':
            return await _verify_saved(store, client, r,
                'version_conflict' if r.asset_save.error_code == 'version_conflict' else None)
        state = await client.video_state(r.video_id)
        p = r.replacement_plan
        if (state_fingerprint(state) != p.source_fingerprint
                or datetime.fromisoformat(remote_version(state.get('updated_at'))) != datetime.fromisoformat(p.source_version)):
            raise PlanError('source_changed')
        if not verify_draft(r, state).ready:
            raise PlanError('draft_changed')
        fresh = audit_assets(state, template_id=r.snapshot.settings.template_id, run_id=r.id, video_id=r.video_id,
                             verified_replacements=r.favorite_replacements, policy=r.asset_audit.policy)
        if fresh.state != 'completed' or fresh.items != r.asset_audit.items or fresh.issues:
            raise PlanError('source_changed')
        if p.state == 'not_needed':
            if p.selections or fresh.counts()['detected'] or not validate_replacements(state, state, ()).ready:
                raise PlanError('source_changed')
            return store.finish_save(r.id, verified_version=state['updated_at'], fingerprint=state_fingerprint(state),
                                     expected_version=r.version)
        save_body(state)  # Check mapping before a request intent can be created.
        favorites = await collect_favorites(client, policy=p.policy)
        for candidate in {choice.candidate.key(): choice.candidate for choice in p.selections}.values():
            await choose_favorite(client, favorites, frozen=candidate, policy=p.policy)
        if make_choices(state, fresh, p.selections[0].candidate) != p.selections:
            raise PlanError('source_changed')
        proposed = apply_replacements(state, p.selections, ephemeral=favorites.raw)
        if state_fingerprint(proposed) != p.result_fingerprint or not validate_replacements(state, proposed, p.selections).ready:
            raise PlanError('transform_invalid')
        body = save_body(proposed)
        r = store.begin_save_request(r.id, request_version=state['updated_at'],
                                    result_url_fingerprint=url_reference_fingerprint(proposed), expected_version=r.version)
        failure = None
        try:
            version = await client.save_video_context(r.video_id, body)
        except Exception as error:
            definite = isinstance(error, InfoXError) and error.status_code in (400, 401, 403, 404, 409, 422)
            r = _record_outcome(store, r, 'rejected' if definite else 'unknown')
            failure = 'version_conflict' if isinstance(error, InfoXError) and error.status_code == 409 else (
                'save_rejected' if definite else 'save_unconfirmed')
        else:
            r = _record_outcome(store, r, 'accepted', version)
        if r.asset_save.state == 'saved_verified':
            return r
        return await _verify_saved(store, client, r, failure)
    except RunError:
        raise
    except Exception as error:
        return store.fail_save(r.id, _error_code(error), expected_version=r.version)
