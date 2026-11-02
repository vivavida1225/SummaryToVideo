"""Typed CAS transitions for the single whole-plan edit save operation."""

import math
from datetime import datetime

from .asset_models import AssetReference, FavoriteReplacement
from .run_models import RunError, now
from .save_models import AssetSave, remote_version


SAVE_STAGES = ('replacing_assets', 'saving_assets', 'verifying_assets')


class SaveStoreMixin:
    def begin_save(self, run_id: str, *, expected_version: int):
        with self._edit(run_id, expected_version) as (r, data):
            plan, save = r.replacement_plan, r.asset_save
            if (r.stage not in ('replacement_planned', 'assets_audited', 'needs_attention', *SAVE_STAGES)
                    or plan.state not in ('ready', 'not_needed') or not plan.checks.ready
                    or not plan.source_version or not plan.source_fingerprint or not plan.result_fingerprint
                    or r.asset_audit.state != 'completed' or not r.tracking.draft.ready
                    or not r.tracking.completion_seen or r.tracking.task_status != 'completed'
                    or r.project.state != 'accepted' or r.generation.state != 'accepted'
                    or not r.video_id or not r.task_id):
                raise RunError('save_not_ready')
            if save.state in ('saved_verified', 'unchanged_verified'):
                raise RunError('save_already_verified')
            if plan.state == 'ready' and (not r.snapshot.settings.auto_replace or not plan.selections):
                raise RunError('save_not_ready')
            if plan.state == 'not_needed' and (plan.selections or r.asset_audit.counts()['detected']):
                raise RunError('save_not_ready')
            # Claim a local check; this is NOT permission to replay a pending POST.
            data.update(stage='verifying_assets' if save.receipt_state != 'not_sent' else 'replacing_assets',
                        asset_save={**save.model_dump(), 'state': 'verifying' if save.receipt_state != 'not_sent'
                                    else 'checking_source', 'error_code': save.error_code if save.receipt_state != 'not_sent' else None})
        return self.get(run_id)

    def begin_save_request(self, run_id: str, *, request_version: str, result_url_fingerprint: str, expected_version: int):
        remote_version(request_version)
        with self._edit(run_id, expected_version) as (r, data):
            if (r.stage != 'replacing_assets' or r.asset_save.state != 'checking_source'
                    or r.asset_save.receipt_state != 'not_sent' or r.replacement_plan.state != 'ready'
                    or not r.replacement_plan.checks.ready
                    or datetime.fromisoformat(request_version) != datetime.fromisoformat(r.replacement_plan.source_version)):
                raise RunError('invalid_stage')
            data.update(stage='saving_assets', asset_save=AssetSave(
                state='saving', receipt_state='intent_recorded', intent_at=now(), request_version=request_version,
                result_url_fingerprint=result_url_fingerprint).model_dump())
        return self.get(run_id)

    def record_save_outcome(self, run_id: str, outcome: str, *, response_version: str | None = None,
                            expected_version: int):
        if outcome not in ('accepted', 'unknown', 'rejected'):
            raise RunError('invalid_outcome')
        with self._edit(run_id, expected_version) as (r, data):
            if r.asset_save.receipt_state not in ('intent_recorded', 'unknown') or r.stage not in SAVE_STAGES:
                raise RunError('invalid_stage')
            if outcome == 'accepted' and not response_version:
                raise RunError('invalid_outcome')
            data.update(stage='verifying_assets', asset_save={**r.asset_save.model_dump(), 'state': 'verifying',
                        'receipt_state': outcome, 'response_version': response_version})
        return self.get(run_id)

    def fail_save(self, run_id: str, code: str, *, expected_version: int):
        with self._edit(run_id, expected_version) as (r, data):
            if r.stage not in SAVE_STAGES:
                raise RunError('invalid_stage')
            data.update(stage='needs_attention', asset_save={**r.asset_save.model_dump(),
                        'state': 'needs_attention', 'checked_at': now(), 'error_code': code})
        return self.get(run_id)

    def finish_save(self, run_id: str, *, verified_version: str, fingerprint: str, expected_version: int,
                    url_fingerprint: str | None = None):
        """Only the coordinator's independent server read may supply this evidence."""
        remote_version(verified_version)
        with self._edit(run_id, expected_version) as (r, data):
            p, s = r.replacement_plan, r.asset_save
            unchanged = p.state == 'not_needed'
            if (r.stage not in SAVE_STAGES or s.state not in ('checking_source', 'verifying')
                    or fingerprint != p.result_fingerprint or not p.checks.ready
                    or (unchanged and s.receipt_state != 'not_sent')
                    or (not unchanged and (p.state != 'ready' or s.receipt_state == 'not_sent'
                                           or verified_version == p.source_version
                                           or not url_fingerprint or url_fingerprint != s.result_url_fingerprint))
                    or (s.response_version is not None and s.response_version != verified_version)):
                raise RunError('verification_required')
            checked = now()
            history = list(r.favorite_replacements)
            for choice in p.selections:
                c, target = choice.candidate, choice.target
                history.append(FavoriteReplacement(run_id=r.id, video_id=r.video_id,
                    scene_index=target.scene_index, scene_id=target.scene_id, layer_id=target.layer_id,
                    item_index=choice.result_item_index,
                    reference=AssetReference(kind='video', bucket=c.bucket, filename=c.segment, segment=c.segment,
                        video_id=c.video_id, frame_index=c.frame_index, timestamp=c.timestamp, start_frame=0,
                        end_frame=min(math.floor(c.duration * 30), choice.slot_frames)),
                    source=c.source, search=p.policy.search, favorites_only=True, saved_verified=True, verified_at=checked))
            data.update(stage='ready_for_editor', favorite_replacements=tuple(x.model_dump() for x in history),
                        asset_save={**s.model_dump(), 'state': 'unchanged_verified' if unchanged else 'saved_verified',
                            'verified_version': verified_version, 'verified_fingerprint': fingerprint,
                            'checked_at': checked, 'error_code': None})
        return self.get(run_id)
