import { describe, expect, it } from 'vitest'
import { assetSummary, currentStep, infoxSteps, productionProgress, publicationProgress } from './infoxFlow'
import type { InfoXRun, PublicationRecord } from './types'

function publication(overrides: Partial<PublicationRecord> = {}): PublicationRecord {
  return { id: 'pub-1', run_id: 'run-1', video_id: 'video-1', attempt_number: 1, previous_publication_id: null,
    created_at: '2026-11-04T00:00:00Z', version: 0, stage: 'rendering', failed_stage: null, error_code: null, progress: 42,
    snapshot: { market_date: '2026-11-04', title: '261104 AI 영상 시황', description: '2026년 11월 4일 AI 영상 시황', privacy_status: 'unlisted' },
    uploaded_verified: false, share_url: null, youtube_video_id: null, clipboard_state: 'not_started', ...overrides }
}

function run(overrides: Partial<InfoXRun> = {}): InfoXRun {
  return { id: 'a'.repeat(32), stage: 'prepared', version: 0, requires_reconciliation: false,
    snapshot: { job_id: 'job-1', revision: 0, text: '원본', text_sha256: '', scenes: [],
      settings: { title: '시황', template_id: 1786, voice_id: 'v', voice_speed: '1.0', auto_replace: true } },
    video_id: null, task_id: null, ...overrides }
}
const ready = run({ stage: 'ready_for_editor', video_id: 'video-1', asset_save: { state: 'saved_verified', receipt_state: 'accepted' } })
const statuses = (input: Parameters<typeof infoxSteps>[0]) => infoxSteps(input).map(step => step.status)

describe('InfoX step model', () => {
  it('keeps production warnings while manual review and publication advance independently', () => {
    const warning = run({ stage: 'needs_attention', video_id: 'video-1', tracking_active: false,
      can_enter_manual_review: true, can_open_editor: false, can_prepare_publication: false })
    const steps = infoxSteps({ run: warning, pending: false, publication: null })
    expect(steps[1]!.status).toBe('attention')
    expect(steps[2]!.status).toBe('action')
    expect(currentStep(steps).key).toBe('publish')
    expect(currentStep(infoxSteps({ run: warning, pending: false, publication: publication() })).key).toBe('publish')
    expect(currentStep(infoxSteps({ run: warning, pending: false, publication: publication({ stage: 'completed' }) })).key).toBe('complete')
  })
  it('maps runs and publications to the four steps and the current one', () => {
    expect(statuses({ run: null, pending: false, publication: null })).toEqual(['action', 'waiting', 'waiting', 'waiting'])
    expect(statuses({ run: null, pending: true, publication: null })).toEqual(['attention', 'waiting', 'waiting', 'waiting'])
    expect(statuses({ run: run(), pending: false, publication: null })).toEqual(['action', 'waiting', 'waiting', 'waiting'])
    expect(statuses({ run: run({ stage: 'generating', tracking_active: true }), pending: false, publication: null }))
      .toEqual(['done', 'active', 'waiting', 'waiting'])
    expect(statuses({ run: run({ stage: 'assets_audited', tracking_active: false }), pending: false, publication: null }))
      .toEqual(['done', 'action', 'waiting', 'waiting'])
    expect(statuses({ run: run({ stage: 'creating_project', requires_reconciliation: true }), pending: false, publication: null })[1]).toBe('attention')
    expect(statuses({ run: run({ stage: 'failed' }), pending: false, publication: null })[1]).toBe('failed')
    expect(statuses({ run: ready, pending: false, publication: null })).toEqual(['done', 'done', 'action', 'waiting'])
    expect(statuses({ run: ready, pending: false, publication: publication() })).toEqual(['done', 'done', 'active', 'waiting'])
    expect(statuses({ run: ready, pending: false, publication: publication({ stage: 'failed' }) })[2]).toBe('failed')
    expect(statuses({ run: ready, pending: false, publication: publication({ stage: 'completed' }) })).toEqual(['done', 'done', 'done', 'done'])
    // Upload verified but only the copy failed: completion stays, with an action.
    expect(statuses({ run: ready, pending: false, publication: publication({ stage: 'clipboard_failed' }) })).toEqual(['done', 'done', 'done', 'action'])
    expect(statuses({ run: ready, pending: false, publication: publication({ stage: 'completed' }), preparingPublication: true })[2]).toBe('action')
    expect(currentStep(infoxSteps({ run: ready, pending: false, publication: null })).key).toBe('publish')
    expect(currentStep(infoxSteps({ run: ready, pending: false, publication: publication({ stage: 'completed' }) })).key).toBe('complete')
  })

  it('does not treat an unverified ready stage as done', () => {
    const unverified = { ...ready, asset_save: { state: 'verifying', receipt_state: 'accepted' } }
    expect(statuses({ run: unverified, pending: false, publication: null })[1]).toBe('attention')
  })

  it('shows numeric progress only for the current task and never a fake percent', () => {
    expect(productionProgress(run({ stage: 'generating', tracking_active: true, tracking: { completion_seen: false, task_status: 'running', progress: 42 } })))
      .toEqual({ label: '시안 제작 중', value: 42 })
    expect(productionProgress(run({ stage: 'generating', tracking_active: true, tracking: { completion_seen: false, task_status: 'running', progress: null } })))
      .toEqual({ label: '시안 제작 중', value: null })
    expect(productionProgress(run({ stage: 'generating', tracking_active: true, tracking: { completion_seen: false, task_status: 'running', progress: 100 } })))
      .toEqual({ label: '결과 확인 중', value: null })
    // The generation progress is not reused for later stages.
    expect(productionProgress(run({ stage: 'auditing_assets', tracking_active: true, tracking: { completion_seen: true, task_status: 'completed', progress: 100 } })))
      .toEqual({ label: '에셋 검사 중', value: null })
    expect(productionProgress(run({ stage: 'draft_ready', tracking_active: false }))).toBeNull()
    expect(publicationProgress(publication({ stage: 'rendering', progress: 42 }))).toEqual({ label: '렌더 중', value: 42 })
    expect(publicationProgress(publication({ stage: 'uploading', progress: 100 }))).toEqual({ label: '결과 확인 중', value: null })
    expect(publicationProgress(publication({ stage: 'saving_metadata', progress: 42 }))).toEqual({ label: '제목·설명 저장 확인 중', value: null })
    expect(publicationProgress(publication({ stage: 'completed' }))).toBeNull()
  })

  it('summarizes detections, unknown assets and replacements', () => {
    const item = (verdict: string) => ({ scene_index: 0, layer_id: 'm', item_index: null, verdict, reason: '',
      reference: { kind: 'image' }, hits: [], metadata_sources: [] })
    const summary = assetSummary(run({ stage: 'replacement_planned',
      asset_audit: { state: 'completed', issues: [], items: [item('detected'), item('detected'), item('unknown'), item('not_detected')] },
      replacement_plan: { state: 'ready', selections: [{} as never, {} as never] } }))
    expect(summary).toMatchObject({ detected: 2, unknown: 1, notDetected: 1, replacements: 2, saveLabel: null })
    expect(assetSummary(run())).toBeNull()
  })
})
