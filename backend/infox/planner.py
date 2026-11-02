"""Read-only phase 5 execution. Durable stable choices, ephemeral proposed JSON."""

from datetime import datetime, timezone

from .assets import audit_assets
from .errors import InfoXError
from .favorites import collect_favorites, choose_favorite
from .plan_models import PlanError, PreservationChecks, ReplacementPlan
from .replacement import apply_replacements, make_choices, state_fingerprint, validate_replacements
from .tracking import verify_draft
from .run_models import RunError


def _now():
    return datetime.now(timezone.utc).isoformat()


async def prepare_plan(store,client,r):
    if r.stage!='selecting_favorites':
        r=store.begin_plan(r.id,expected_version=r.version)
    try:
        state=await client.video_state(r.video_id)
        if not verify_draft(r,state).ready:
            raise PlanError('draft_changed')
        fresh=audit_assets(state,template_id=r.snapshot.settings.template_id,run_id=r.id,video_id=r.video_id,
                           verified_replacements=r.favorite_replacements,policy=r.asset_audit.policy)
        if (fresh.state!='completed' or fresh.source_version!=r.asset_audit.source_version
                or fresh.items!=r.asset_audit.items or fresh.issues):
            raise PlanError('source_changed')
        fingerprint=state_fingerprint(state)
        previous=r.replacement_plan
        if previous.selections and (previous.source_version!=fresh.source_version or previous.source_fingerprint!=fingerprint):
            raise PlanError('source_changed')
        detected=tuple(item for item in fresh.items if item.verdict=='detected')
        if not detected:
            result=ReplacementPlan(state='not_needed',source_version=fresh.source_version,
                source_fingerprint=fingerprint,result_fingerprint=fingerprint,
                checks=validate_replacements(state,state,()),checked_at=_now())
        elif not r.snapshot.settings.auto_replace:
            result=ReplacementPlan(state='disabled',source_version=fresh.source_version,
                                   source_fingerprint=fingerprint,checked_at=_now())
        else:
            favorites=await collect_favorites(client,policy=previous.policy)
            frozen=previous.selections[0].candidate if previous.selections else None
            selected=await choose_favorite(client,favorites,frozen=frozen,policy=previous.policy)
            choices=make_choices(state,fresh,selected)
            r=store.freeze_choices(r.id,choices,source_fingerprint=fingerprint,source_version=fresh.source_version,
                                   evidence=favorites.evidence,expected_version=r.version)
            proposed=apply_replacements(state,choices,ephemeral=favorites.raw)
            checks=validate_replacements(state,proposed,choices)
            if not checks.ready:
                raise PlanError('transform_invalid')
            result=ReplacementPlan(**{**r.replacement_plan.model_dump(),'state':'ready','checks':checks,
                'result_fingerprint':state_fingerprint(proposed),'checked_at':_now(),'error_code':None})
    except InfoXError as error:
        code='authentication_required' if error.code in ('auth_expired','permission_denied','configuration_error') else 'read_failed'
        result=ReplacementPlan(**{**r.replacement_plan.model_dump(),'state':'needs_attention','error_code':code,
                                  'checks':PreservationChecks(),'result_fingerprint':None,'checked_at':_now()})
    except PlanError as error:
        result=ReplacementPlan(**{**r.replacement_plan.model_dump(),'state':'needs_attention','error_code':error.code,
                                  'checks':PreservationChecks(),'result_fingerprint':None,'checked_at':_now()})
    except RunError:
        raise
    except Exception:
        result=ReplacementPlan(**{**r.replacement_plan.model_dump(),'state':'needs_attention','error_code':'read_failed',
                                  'checks':PreservationChecks(),'result_fingerprint':None,'checked_at':_now()})
    return store.record_plan(r.id,result,expected_version=r.version)
