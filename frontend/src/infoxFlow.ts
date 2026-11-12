import { manualReviewAvailable } from './infoxEditor'
import type { InfoXRun, PublicationRecord } from './types'

// Display-only model for the InfoX panel. It never decides whether a request
// may be sent; execution conditions stay in the existing handlers and backend.

export type InfoxStepKey = 'prepare' | 'produce' | 'publish' | 'complete'
export type InfoxStepStatus = 'waiting' | 'active' | 'action' | 'done' | 'attention' | 'failed'

export const stepStatusLabels: Record<InfoxStepStatus, string> = {
  waiting: '대기', active: '진행 중', action: '사용자 작업 필요', done: '완료', attention: '확인 필요', failed: '실패',
}

export const stepTitles: Record<InfoxStepKey, string> = {
  prepare: '제작 준비', produce: '자동 제작·검사', publish: 'InfoX 수기 검토·수정·게시', complete: '완료',
}

export interface InfoxStep { key: InfoxStepKey; number: number; title: string; status: InfoxStepStatus }

/** value null means indeterminate: never invent a percentage. */
export interface ProgressDisplay { label: string; value: number | null }

export const infoxActiveStages = new Set(['creating_project', 'project_created', 'starting_generation', 'generating',
  'checking_draft', 'auditing_assets', 'selecting_favorites', 'replacing_assets', 'saving_assets', 'verifying_assets'])
const publishActiveStages = new Set(['prepared', 'preflighting', 'starting_render', 'rendering', 'generating_metadata',
  'saving_metadata', 'starting_upload', 'uploading', 'copying_link'])

export interface InfoxFlowInput {
  run: InfoXRun | null
  pending: boolean
  publication: PublicationRecord | null
  preparingPublication?: boolean
}

export function runTracking(run: InfoXRun): boolean {
  return run.tracking_active ?? infoxActiveStages.has(run.stage)
}

function produceStatus(run: InfoXRun): InfoxStepStatus {
  if (run.stage === 'failed') return 'failed'
  if (run.requires_reconciliation || run.stage === 'needs_attention') return 'attention'
  if (run.stage === 'ready_for_editor') return ['saved_verified', 'unchanged_verified'].includes(run.asset_save?.state ?? '') ? 'done' : 'attention'
  if (runTracking(run)) return 'active'
  if (run.stage === 'prepared') return 'waiting'
  return 'action' // draft_ready, assets_audited, replacement_planned: a manual step is available.
}

function publishStatus(record: PublicationRecord | null, preparing: boolean): InfoxStepStatus {
  if (!record || preparing) return 'action'
  if (publishActiveStages.has(record.stage)) return 'active'
  if (record.stage === 'failed') return 'failed'
  if (record.stage === 'needs_attention') return 'attention'
  return 'done' // completed or clipboard_failed: the upload itself was verified.
}

export function infoxSteps(input: InfoxFlowInput): InfoxStep[] {
  const { run, pending, publication } = input
  const status: Record<InfoxStepKey, InfoxStepStatus> = { prepare: 'action', produce: 'waiting', publish: 'waiting', complete: 'waiting' }
  if (pending) status.prepare = 'attention'
  else if (run) {
    status.prepare = run.stage === 'prepared' && !run.requires_reconciliation ? 'action' : 'done'
    status.produce = produceStatus(run)
    if (manualReviewAvailable(run) || run.review_mode === 'manual_review' || publication || status.produce === 'done') {
      status.publish = publishStatus(publication, !!input.preparingPublication)
      if (status.publish === 'done') status.complete = publication?.stage === 'clipboard_failed' ? 'action' : 'done'
    }
  }
  return (['prepare', 'produce', 'publish', 'complete'] as const).map((key, index) =>
    ({ key, number: index + 1, title: stepTitles[key], status: status[key] }))
}

/** The step the user should look at now: the first one that is not done. */
export function currentStep(steps: InfoxStep[]): InfoxStep {
  const publish = steps.find(step => step.key === 'publish')!
  if (publish.status === 'done') return steps.find(step => step.key === 'complete')!
  if (publish.status !== 'waiting') return publish
  return steps.find(step => step.status !== 'done') ?? steps[steps.length - 1]!
}

const productionLabels: Record<string, string> = {
  creating_project: '프로젝트 생성 요청 중', project_created: '영상 ID 확인 중', starting_generation: '제작 시작 요청 중',
  generating: '시안 제작 중', checking_draft: '결과 확인 중', auditing_assets: '에셋 검사 중',
  selecting_favorites: '교체 후보 확인 중', replacing_assets: '교체 대상 확인 중', saving_assets: '편집 데이터 저장 중',
  verifying_assets: '저장 결과 확인 중',
}

/** Progress for the current production task only; never reused for later stages. */
export function productionProgress(run: InfoXRun | null): ProgressDisplay | null {
  if (!run || !runTracking(run) || !infoxActiveStages.has(run.stage)) return null
  const label = productionLabels[run.stage] ?? '진행 상태 확인 중'
  if (run.stage !== 'generating') return { label, value: null }
  const progress = run.tracking?.progress
  if (progress == null || !Number.isFinite(progress)) return { label, value: null }
  if (progress >= 100 || run.tracking?.completion_seen) return { label: '결과 확인 중', value: null }
  return { label, value: Math.max(0, Math.min(100, progress)) }
}

const publishLabels: Record<string, string> = {
  prepared: '게시 준비 중', preflighting: '게시 조건 확인 중', starting_render: '렌더 요청 중', rendering: '렌더 중',
  generating_metadata: '유튜브 정보 생성 중', saving_metadata: '제목·설명 저장 확인 중', starting_upload: '업로드 요청 중',
  uploading: '일부공개 업로드 중', copying_link: '링크 복사 중',
}

export function publicationProgress(record: PublicationRecord | null): ProgressDisplay | null {
  if (!record || !publishActiveStages.has(record.stage)) return null
  const label = publishLabels[record.stage] ?? '게시 상태 확인 중'
  if (!['rendering', 'generating_metadata', 'uploading'].includes(record.stage)) return { label, value: null }
  const progress = record.progress
  if (progress == null || !Number.isFinite(progress)) return { label, value: null }
  if (progress >= 100) return { label: '결과 확인 중', value: null }
  return { label, value: Math.max(0, Math.min(100, progress)) }
}

export interface AssetSummary {
  audited: boolean
  detected: number
  notDetected: number
  unknown: number
  exempt: number
  replacements: number | null
  saveLabel: string | null
}

const saveLabels: Record<string, string> = {
  checking_source: '저장 전 확인 중', saving: '저장 중', verifying: '저장 결과 확인 중',
  saved_verified: '교체 저장·검증 완료', unchanged_verified: '변경 없음 · 재조회 검증 완료', needs_attention: '저장 확인 필요',
}

export function assetSummary(run: InfoXRun | null): AssetSummary | null {
  const audit = run?.asset_audit
  if (!run || !audit || audit.state === 'not_started') return null
  const count = (verdict: string) => audit.items.filter(item => item.verdict === verdict).length
  const plan = run.replacement_plan
  return {
    audited: audit.state !== 'running',
    detected: count('detected'), notDetected: count('not_detected'), unknown: count('unknown'), exempt: count('favorite_exempt'),
    replacements: plan && !['not_started', 'selecting', 'selected'].includes(plan.state) ? plan.selections.length : null,
    saveLabel: run.asset_save && run.asset_save.state !== 'not_started' ? saveLabels[run.asset_save.state] ?? '저장 기록 확인 필요' : null,
  }
}
