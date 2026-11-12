<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api, ApiError, bootstrapSession } from './api'
import ResultPanel from './components/ResultPanel.vue'
import StatusTimeline from './components/StatusTimeline.vue'
import InfoXAssetAudit from './components/InfoXAssetAudit.vue'
import InfoXAssetSave from './components/InfoXAssetSave.vue'
import InfoXReplacementPlan from './components/InfoXReplacementPlan.vue'
import InfoXProgress from './components/InfoXProgress.vue'
import InfoXDownloads from './components/InfoXDownloads.vue'
import InfoXPublishing from './components/InfoXPublishing.vue'
import InfoXStep from './components/InfoXStep.vue'
import { EditorNavigation, editorEligible, editorUrl, manualReviewAvailable } from './infoxEditor'
import { assetSummary, currentStep, infoxActiveStages, infoxSteps, productionProgress, publicationProgress, runTracking,
  stepStatusLabels, type InfoxStep, type InfoxStepKey } from './infoxFlow'
import { PublishingControl, formatPublishMetadata, publishErrors, publishStages } from './infoxPublishing'
import { DEFAULT_INFOX_TITLE_TEMPLATE, formatInfoxTitle, validInfoxTitleTemplate } from './infoxTitle'
import type { InfoXPrepareInput, InfoXRun, Job, SessionInfo, SourceFile } from './types'

const JOB_STORAGE_KEY = 'market-compressor.job-id'
const MODEL_STORAGE_KEY = 'market-compressor.model'
const TRANSCRIBE_NUMBERS_STORAGE_KEY = 'market-compressor.transcribe-numbers'
const INFOX_TITLE_TEMPLATE_STORAGE_KEY = 'market-compressor.infox-title-template'
const TERMINAL_STATES = new Set(['completed', 'failed', 'needs_review'])

const session = ref<SessionInfo | null>(null)
const selectedModel = ref('')
const selectedProvider = computed(() => session.value?.models.find(model => model.id === selectedModel.value)?.provider)
const configuredKeys = computed(() => {
  const provider = selectedProvider.value
  return provider ? session.value?.configured_keys_by_provider[provider] ?? [] : []
})
const providerLabel = computed(() => selectedProvider.value === 'openai' ? 'OpenAI' : 'Gemini')
const transcribeNumbers = ref(false)
const files = ref<SourceFile[]>([])
const sourceMode = ref<'clipboard' | 'file'>('clipboard')
const clipboardText = ref('')
const clipboardFormat = ref<'text' | 'html'>('text')
const selectedFile = ref('')
const job = ref<Job | null>(null)
const loading = ref(true)
const actionBusy = ref(false)
const fetchingMyasset = ref(false)
const myassetSettingsOpen = ref(false)
const myassetSettingsButton = ref<HTMLButtonElement | null>(null)
const myassetSettingsPanel = ref<HTMLDivElement | null>(null)
const todayParts = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
}).formatToParts(new Date())
const myassetDate = ref(['year', 'month', 'day'].map(part => todayParts.find(item => item.type === part)!.value).join('-'))
const myassetGubun = ref<number | string>(30)
const pageError = ref('')
const sourceError = ref('')
const announcement = ref('')
const compressedDraft = ref('')
const savedCompressed = ref('')
const savingCompressed = ref(false)
const compressedSaveError = ref('')
const composingCompressed = ref(false)
const infoxRun = ref<InfoXRun | null>(null)
const infoxLoading = ref(false)
const infoxStarting = ref(false)
const editorNavigation = new EditorNavigation({
  latestEditor: id => withInfoxSession(() => api.latestEditor(id)),
  claimEditor: (id, body) => withInfoxSession(() => api.claimEditor(id, body)),
  updateEditor: (id, attempt, body) => withInfoxSession(() => api.updateEditor(id, attempt, body)),
})
const editorAttempt = editorNavigation.attempt
const editorMessage = editorNavigation.message
const editorBusy = computed(() => editorNavigation.busy.value || editorNavigation.navigating.value || publishing.busy.value ||
  (!!publishing.record.value && !['completed', 'failed', 'needs_attention', 'clipboard_failed'].includes(publishing.record.value.stage)))
watch(infoxRun, record => { if (record) void editorNavigation.advance(record) })

async function reopenEditor() {
  if (!infoxRun.value || !manualReviewAvailable(infoxRun.value) || infoxStarting.value) return
  const record = infoxRun.value
  editorNavigation.reserve()
  infoxStarting.value = true
  try {
    if (record.review_mode !== 'manual_review' && record.can_enter_manual_review !== undefined) {
      const updated = await withInfoxSession(() => api.manualReviewInfox(record.id, record.version))
      if (infoxRun.value?.id !== record.id) { editorNavigation.release(); return }
      infoxRun.value = updated
    }
    await editorNavigation.begin(record.id, 'manual')
    if (infoxRun.value?.id === record.id) await editorNavigation.advance(infoxRun.value)
  } catch (error) {
    editorNavigation.release()
    infoxError.value = friendlyError(error)
    try {
      const latest = await withInfoxSession(() => api.infoxRun(record.id))
      if (infoxRun.value?.id === record.id) infoxRun.value = latest
    } catch { /* Keep the warning; never repeat the handoff or production automatically. */ }
  } finally { infoxStarting.value = false }
}
// Owned here so step layout changes never dispose it or replay its requests.
const publishing = new PublishingControl(api)
const publishingRunId = computed(() => infoxRun.value?.video_id ? infoxRun.value.id : null)
const infoxEditorReady = computed(() => !!infoxRun.value && (infoxRun.value.can_prepare_publication ?? editorEligible(infoxRun.value)))
watch(publishingRunId, id => {
  if (id) void publishing.restore(id, infoxEditorReady.value)
  else publishing.dispose()
})
watch(infoxEditorReady, eligible => { if (publishingRunId.value) void publishing.setEligibility(eligible) })
watch(() => publishing.record.value?.stage, async stage => {
  const run = infoxRun.value
  if (!run || !stage || !['completed', 'failed', 'needs_attention', 'clipboard_failed'].includes(stage)) return
  if (run.can_prepare_publication !== false) return
  try {
    const latest = await withInfoxSession(() => api.infoxRun(run.id))
    if (infoxRun.value?.id === run.id) infoxRun.value = latest
  } catch { /* The existing publication and its read-only history remain available. */ }
})
let infoxPollTimer: number | undefined
let editorTabTimer: number | undefined
const infoxStageLabels: Record<string, string> = {
  prepared: '전송본 확정 · 제작 요청 전', creating_project: 'InfoX 프로젝트 생성 요청 중',
  project_created: '프로젝트 접수 · 영상 ID 확보', starting_generation: '시안 제작 시작 요청 중',
  generating: '시안 제작 중 · 현재 작업의 완료 신호 대기', checking_draft: '완료 신호 수신 · 대본·음성·자막 확인 중',
  draft_ready: '시안 준비 확인 · 에셋 검사를 시작할 수 있습니다.',
  auditing_assets: '실제 배정 에셋의 기업명 검사 중', assets_audited: '기업 에셋 검사 완료 · 교체는 다음 단계입니다.',
  selecting_favorites: '즐겨찾기 후보와 로컬 교체 계획 확인 중', replacement_planned: '로컬 교체 계획 준비 · 서버 저장 전',
  replacing_assets: '최신 상태와 교체 대상 확인 중', saving_assets: 'InfoX 편집 데이터 저장 중',
  verifying_assets: '서버 재조회 · 적용과 보존 검증 중', ready_for_editor: '서버 데이터 검증 완료 · 수동 편집기 확인 가능',
  needs_attention: '확인이 필요합니다. 기존 실행 상태를 확인하세요.', failed: '제작 실패가 확인되었습니다.',
}
const infoxErrorLabels: Record<string, string> = {
  authentication_required: 'InfoX 백엔드 인증 설정을 확인하세요.',
  project_acceptance_unverifiable: '프로젝트 접수 여부를 확정하지 못했습니다. 새 프로젝트를 자동 생성하지 않습니다.',
  generation_acceptance_unverifiable: '제작 시작 접수 여부를 확정하지 못했습니다. 기존 영상·작업부터 확인해야 합니다.',
  completion_unconfirmed: '현재 작업의 실제 완료 신호를 확인하지 못했습니다.',
  draft_not_ready: '완료 신호 뒤 장면·음성·자막의 필수 데이터와 음성 설정을 확인하지 못했습니다.',
  remote_task_failed: '현재 작업의 실패 이벤트를 확인했습니다.',
  connection_interrupted: '이벤트 연결이 중단되어 같은 영상·작업을 다시 확인하고 있습니다.',
  internal_error: '추적을 계속하지 못했습니다. 보관된 실행 상태를 다시 확인하세요.',
}
const infoxError = ref('')
const infoxReviewConfirmed = ref(false)
const storedInfoxTitleTemplate = readStored('localStorage', INFOX_TITLE_TEMPLATE_STORAGE_KEY)
const infoxTitleTemplate = ref(storedInfoxTitleTemplate && validInfoxTitleTemplate(storedInfoxTitleTemplate)
  ? storedInfoxTitleTemplate : DEFAULT_INFOX_TITLE_TEMPLATE)
const infoxTitleTemplateDraft = ref(infoxTitleTemplate.value)
const infoxTitleTemplateError = ref('')
const infoxTitleTemplateSaved = ref(false)
const infoxTitleTemplateValid = computed(() => validInfoxTitleTemplate(infoxTitleTemplateDraft.value))
const infoxTitle = ref(formatInfoxTitle(infoxTitleTemplate.value))
const infoxAutoReplace = ref(true)
const infoxPending = ref<InfoXPrepareInput | null>(null)
const infoxNeedsReview = computed(() => job.value?.state !== 'completed' || !!job.value?.validation_issues?.length)
const infoxChanged = computed(() => !!infoxRun.value && (compressedDirty.value ||
  infoxRun.value.snapshot.revision !== (job.value?.revision ?? 0) ||
  infoxRun.value.snapshot.settings.title !== infoxTitle.value ||
  infoxRun.value.snapshot.settings.auto_replace !== infoxAutoReplace.value))
let saveQueue: Promise<void> | null = null
const compressedDirty = computed(() => compressedDraft.value !== savedCompressed.value)
const manuallyEdited = computed(() => !!job.value?.edited_at || compressedDirty.value)
const draftCharCount = computed(() => Array.from(compressedDraft.value.replace(/[\r\n]/g, '')).length)
let pollTimer: number | undefined
let disposed = false

const isRunning = computed(() => job.value !== null && !TERMINAL_STATES.has(job.value.state))
const inputBusy = computed(() => loading.value || actionBusy.value || isRunning.value)
const myassetSettingsError = computed(() => {
  const date = myassetDate.value
  const parsed = new Date(`${date}T00:00:00Z`)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date.startsWith('0000') || Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    return '날짜를 YYYY-MM-DD 형식의 유효한 날짜로 입력하세요.'
  }
  const gubun = myassetGubun.value
  if (gubun === '' || !Number.isSafeInteger(Number(gubun)) || Number(gubun) < 0) return 'gubun은 0 이상의 정수로 입력하세요.'
  return ''
})
const canRun = computed(() => {
  if (loading.value || actionBusy.value || isRunning.value || !selectedModel.value) return false
  return sourceMode.value === 'clipboard' ? clipboardText.value.trim().length > 0 : selectedFile.value.length > 0
})

const fileSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`
}

function friendlyError(error: unknown) {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return '예기치 못한 오류가 발생했습니다.'
}

function readStored(storage: 'localStorage' | 'sessionStorage', key: string) {
  try { return window[storage].getItem(key) } catch { return null }
}

function writeStored(storage: 'localStorage' | 'sessionStorage', key: string, value: string | null) {
  try {
    if (value === null) window[storage].removeItem(key)
    else window[storage].setItem(key, value)
  } catch { /* Storage can be blocked; keep this page usable. */ }
}

function saveInfoxTitleTemplate() {
  if (!infoxTitleTemplateValid.value || inputBusy.value || infoxLoading.value || infoxPending.value) return
  infoxTitleTemplateError.value = ''
  infoxTitleTemplateSaved.value = false
  try {
    window.localStorage.setItem(INFOX_TITLE_TEMPLATE_STORAGE_KEY, infoxTitleTemplateDraft.value)
  } catch {
    infoxTitleTemplateError.value = '기본 제목을 저장하지 못했습니다. 브라우저 저장소를 확인하세요.'
    return
  }
  infoxTitleTemplate.value = infoxTitleTemplateDraft.value
  infoxTitle.value = formatInfoxTitle(infoxTitleTemplate.value)
  infoxTitleTemplateSaved.value = true
}

watch(infoxTitleTemplateDraft, () => {
  infoxTitleTemplateError.value = ''
  infoxTitleTemplateSaved.value = false
})

function rememberModel() {
  writeStored('localStorage', MODEL_STORAGE_KEY, selectedModel.value)
}

function rememberTranscribeNumbers() {
  writeStored('localStorage', TRANSCRIBE_NUMBERS_STORAGE_KEY, String(transcribeNumbers.value))
}

function rememberJob(nextJob: Job) {
  if (job.value?.id !== nextJob.id || !compressedDirty.value) {
    compressedDraft.value = nextJob.compressed ?? ''
    savedCompressed.value = nextJob.compressed ?? ''
    compressedSaveError.value = ''
  }
  job.value = nextJob
  writeStored('sessionStorage', JOB_STORAGE_KEY, nextJob.id)
}

function editCompressed(text: string) {
  if (text !== compressedDraft.value) infoxReviewConfirmed.value = false
  compressedDraft.value = text
  if (!composingCompressed.value) void flushCompressed().catch(() => {})
}

const infoxPendingKey = (id: string) => `market-compressor.infox-pending.${id}`

function stopInfoxPolling() {
  if (infoxPollTimer !== undefined) window.clearTimeout(infoxPollTimer)
  infoxPollTimer = undefined
}

function pollInfox(id: string) {
  stopInfoxPolling()
  infoxPollTimer = window.setTimeout(async () => {
    if (disposed || infoxRun.value?.id !== id) return
    try {
      const record = await withInfoxSession(() => api.infoxRun(id))
      if (disposed || infoxRun.value?.id !== id) return
      infoxRun.value = record
      if (record.tracking_active || (record.tracking_active === undefined &&
          (infoxActiveStages.has(record.stage) || record.stage === 'prepared'))) pollInfox(id)
    } catch (error) {
      if (!disposed && infoxRun.value?.id === id) infoxError.value = friendlyError(error)
    }
  }, 1000)
}

async function requestInfoxStart(id: string, jobId: string | undefined) {
  // Synchronous up to its first await: inside a click it opens the waiting tab
  // itself, otherwise it consumes the tab reserved earlier in the same click.
  const browserClaim = editorNavigation.begin(id, 'automatic')
  try {
    await browserClaim
    const record = await withInfoxSession(() => api.startInfox(id))
    if (disposed || job.value?.id !== jobId || infoxRun.value?.id !== id) return
    infoxRun.value = record
    pollInfox(id)
  } catch (error) {
    if (!disposed && infoxRun.value?.id === id) {
      infoxError.value = '요청 결과가 불명확합니다. 보관된 실행을 조회합니다. ' + friendlyError(error)
      pollInfox(id) // Only GET; never replay an uncertain start POST.
    }
  }
}

async function startInfox() {
  if (!infoxRun.value || infoxStarting.value || infoxLoading.value || inputBusy.value) return
  infoxStarting.value = true
  infoxError.value = ''
  try { await requestInfoxStart(infoxRun.value.id, job.value?.id) } finally { infoxStarting.value = false }
}

// One click: check inputs, reserve the editor tab, save the latest script,
// confirm that revision and settings, then start production of that snapshot.
async function beginInfoxProduction() {
  if (!job.value || infoxPending.value || infoxStartBlocker.value || inputBusy.value || infoxStarting.value) return
  if (infoxRun.value && !infoxRestartable.value) return
  const id = job.value.id
  actionBusy.value = true
  infoxStarting.value = true
  infoxError.value = ''
  editorNavigation.reserve() // Must stay before the first await (click activation).
  let attached = false
  try {
    await flushCompressed()
    if (disposed || job.value?.id !== id) return
    if (composingCompressed.value || compressedDirty.value || compressedSaveError.value) {
      throw new Error('최신 대본 저장을 완료한 뒤 다시 시작하세요.')
    }
    const pending: InfoXPrepareInput = {
      intent_id: crypto.randomUUID(), revision: job.value.revision ?? 0,
      settings: { title: infoxTitle.value, auto_replace: infoxAutoReplace.value },
      review_confirmed: infoxReviewConfirmed.value, new_run: false,
    }
    infoxPending.value = pending
    writeStored('sessionStorage', infoxPendingKey(id), JSON.stringify(pending))
    let record: InfoXRun
    try {
      record = await withInfoxSession(() => api.prepareInfox(id, pending))
    } catch (error) {
      if (disposed || job.value?.id !== id) return
      // A received 4xx (except session expiry) proves the preparation was rejected.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 403) {
        infoxPending.value = null
        writeStored('sessionStorage', infoxPendingKey(id), null)
        infoxError.value = friendlyError(error)
      } else {
        infoxError.value = '전송본 확정 응답을 확인하지 못했습니다. 제작은 시작하지 않았습니다. 확정 결과 다시 확인을 누르세요. ' + friendlyError(error)
      }
      return
    }
    if (disposed || job.value?.id !== id) return
    infoxRun.value = record
    infoxPending.value = null
    writeStored('sessionStorage', infoxPendingKey(id), null)
    if (record.stage !== 'prepared' || record.requires_reconciliation) {
      // The same snapshot already has a run: show it, never start it again.
      announcement.value = '같은 전송본의 기존 실행이 있어 기존 상태를 표시합니다.'
      if (runTracking(record)) pollInfox(record.id)
      return
    }
    announcement.value = 'InfoX 전송본을 확정했습니다. 제작을 요청합니다.'
    attached = true
    actionBusy.value = false
    await requestInfoxStart(record.id, id)
  } catch (error) {
    if (!disposed && job.value?.id === id) infoxError.value = friendlyError(error)
  } finally {
    if (!attached) editorNavigation.release() // Close only the tab this click opened.
    actionBusy.value = false
    infoxStarting.value = false
  }
}

async function auditInfox() {
  if (!infoxRun.value || infoxStarting.value || infoxLoading.value || inputBusy.value) return
  const id = infoxRun.value.id
  infoxStarting.value = true
  infoxError.value = ''
  try {
    const record = await withInfoxSession(() => api.auditInfox(id))
    if (disposed || infoxRun.value?.id !== id) return
    infoxRun.value = record
    pollInfox(id)
  } catch (error) {
    if (!disposed && infoxRun.value?.id === id) {
      infoxError.value = '검사 요청 결과를 조회합니다. ' + friendlyError(error)
      pollInfox(id)
    }
  } finally { infoxStarting.value = false }
}

async function planInfox() {
  if (!infoxRun.value || infoxStarting.value || infoxLoading.value || inputBusy.value) return
  const id = infoxRun.value.id
  infoxStarting.value = true
  infoxError.value = ''
  try {
    const record = await withInfoxSession(() => api.planInfox(id))
    if (disposed || infoxRun.value?.id !== id) return
    infoxRun.value = record
    pollInfox(id)
  } catch (error) {
    if (!disposed && infoxRun.value?.id === id) {
      infoxError.value = '보관된 계획을 조회합니다. ' + friendlyError(error)
      pollInfox(id)
    }
  } finally { infoxStarting.value = false }
}

async function withInfoxSession<T>(action: () => Promise<T>): Promise<T> {
  try { return await action() } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 403) throw error
    session.value = await bootstrapSession()
    return action()
  }
}

async function saveInfox() {
  if (!infoxRun.value || infoxStarting.value || infoxLoading.value || inputBusy.value) return
  const id = infoxRun.value.id
  infoxStarting.value = true
  infoxError.value = ''
  try {
    const record = await withInfoxSession(() => api.saveInfox(id))
    if (disposed || infoxRun.value?.id !== id) return
    infoxRun.value = record
    pollInfox(id)
  } catch (error) {
    if (!disposed && infoxRun.value?.id === id) {
      infoxError.value = '저장 기록과 서버 적용 상태를 조회합니다. ' + friendlyError(error)
      pollInfox(id)
    }
  } finally { infoxStarting.value = false }
}

watch(() => job.value?.id, async id => {
  stopInfoxPolling()
  editorNavigation.reset()
  infoxRun.value = null
  infoxError.value = ''
  infoxReviewConfirmed.value = false
  infoxPending.value = null
  infoxTitle.value = formatInfoxTitle(infoxTitleTemplate.value)
  infoxAutoReplace.value = true
  if (!id) return
  try {
    const pending = readStored('sessionStorage', infoxPendingKey(id))
    if (pending) {
      const parsed = JSON.parse(pending) as InfoXPrepareInput
      if (typeof parsed.intent_id === 'string' && Number.isInteger(parsed.revision) &&
          typeof parsed.settings?.title === 'string' && typeof parsed.settings.auto_replace === 'boolean' &&
          typeof parsed.review_confirmed === 'boolean' && typeof parsed.new_run === 'boolean') {
        infoxPending.value = parsed
        infoxTitle.value = parsed.settings.title
        infoxAutoReplace.value = parsed.settings.auto_replace
      }
    }
  } catch { infoxError.value = '이전 요청을 읽지 못했습니다. 보관된 전송본을 확인하세요.' }
  infoxLoading.value = true
  try {
    const restored = await withInfoxSession(() => api.latestInfox(id))
    if (disposed || job.value?.id !== id) return
    infoxRun.value = restored
    if (restored) void editorNavigation.restore(restored.id)
    if (restored && (restored.tracking_active || infoxActiveStages.has(restored.stage))) pollInfox(restored.id)
    if (restored && !infoxPending.value) {
      infoxTitle.value = restored.snapshot.settings.title
      infoxAutoReplace.value = restored.snapshot.settings.auto_replace
    }
  } catch (error) {
    if (!disposed && job.value?.id === id) infoxError.value = friendlyError(error)
  } finally {
    if (job.value?.id === id) infoxLoading.value = false
  }
})

async function prepareInfox() {
  if (!job.value || inputBusy.value || infoxLoading.value || composingCompressed.value) return
  if (!infoxPending.value && infoxNeedsReview.value && !infoxReviewConfirmed.value) return
  const id = job.value.id
  actionBusy.value = true
  infoxError.value = ''
  try {
    if (!infoxPending.value) {
      await flushCompressed()
      if (disposed || job.value?.id !== id) return
      if (composingCompressed.value || compressedDirty.value || compressedSaveError.value) {
        throw new Error('최신 대본 저장을 완료한 뒤 확정하세요.')
      }
      infoxPending.value = {
        intent_id: crypto.randomUUID(), revision: job.value.revision ?? 0,
        settings: { title: infoxTitle.value, auto_replace: infoxAutoReplace.value },
        review_confirmed: infoxReviewConfirmed.value, new_run: false,
      }
      writeStored('sessionStorage', infoxPendingKey(id), JSON.stringify(infoxPending.value))
    }
    // A lost response is retried with the exact same intent, revision and settings.
    const pending = infoxPending.value
    const record = await withInfoxSession(() => api.prepareInfox(id, pending))
    if (disposed || job.value?.id !== id) return
    infoxRun.value = record
    infoxPending.value = null
    writeStored('sessionStorage', infoxPendingKey(id), null)
    announcement.value = 'InfoX 전송본을 확정해 보관했습니다.'
  } catch (error) {
    if (!disposed && job.value?.id === id) {
      infoxError.value = friendlyError(error)
      // A received 4xx (except session expiry) proves the preparation was rejected.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 403) {
        infoxPending.value = null
        writeStored('sessionStorage', infoxPendingKey(id), null)
      }
    }
  } finally { actionBusy.value = false }
}

function composeCompressed(active: boolean) {
  composingCompressed.value = active
  if (!active) void flushCompressed().catch(() => {})
}

async function flushCompressed(): Promise<void> {
  if (composingCompressed.value) throw new Error('한글 입력을 완료한 뒤 다시 시도하세요.')
  if (saveQueue) return saveQueue
  if (!compressedDirty.value || !job.value) return
  const id = job.value.id
  savingCompressed.value = true
  compressedSaveError.value = ''
  saveQueue = (async () => {
    while (!disposed && job.value?.id === id && compressedDirty.value && !composingCompressed.value) {
      const text = compressedDraft.value
      const saved = await api.saveCompressed(id, text, job.value.revision ?? 0)
      if (disposed || job.value?.id !== id) return
      // A response acknowledges only its snapshot; never replace newer local input.
      job.value = saved
      savedCompressed.value = saved.compressed ?? ''
    }
    if (composingCompressed.value) throw new Error('한글 입력을 완료한 뒤 다시 시도하세요.')
  })().catch(error => {
    compressedSaveError.value = friendlyError(error)
    throw error
  }).finally(() => {
    savingCompressed.value = false
    saveQueue = null
  })
  return saveQueue
}

function retryCompressedSave() {
  void flushCompressed().catch(() => {})
}

function warnUnsaved(event: BeforeUnloadEvent) {
  if (!compressedDirty.value && !savingCompressed.value) return
  event.preventDefault()
  event.returnValue = ''
}

function stopPolling() {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

function schedulePoll() {
  stopPolling()
  if (disposed || !job.value || TERMINAL_STATES.has(job.value.state)) return
  pollTimer = window.setTimeout(pollJob, 1000)
}

async function pollJob() {
  if (!job.value) return
  try {
    let updated: Job
    try {
      updated = await api.job(job.value.id)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 403) throw error
      session.value = await bootstrapSession()
      updated = await api.job(job.value.id)
    }
    rememberJob(updated)
    pageError.value = ''
  } catch (error) {
    pageError.value = friendlyError(error)
    if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
      writeStored('sessionStorage', JOB_STORAGE_KEY, null)
      job.value = null
    }
  } finally {
    schedulePoll()
  }
}

async function readClipboard() {
  const clipboard = await api.clipboard()
  clipboardText.value = clipboard.text
  clipboardFormat.value = clipboard.format
}

async function recoverJob(id: string) {
  try {
    const recovered = await api.job(id)
    rememberJob(recovered)
    schedulePoll()
    return true
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      writeStored('sessionStorage', JOB_STORAGE_KEY, null)
      return false
    }
    throw error
  }
}

onMounted(async () => {
  editorTabTimer = window.setInterval(() => { void editorNavigation.checkClosed() }, 1000)
  window.addEventListener('beforeunload', warnUnsaved)
  document.addEventListener('click', dismissMyassetSettings, true)
  try {
    session.value = await bootstrapSession()
    const savedModel = readStored('localStorage', MODEL_STORAGE_KEY)
    selectedModel.value = session.value.models.some(model => model.id === savedModel) ? savedModel! : session.value.model
    rememberModel()
    transcribeNumbers.value = readStored('localStorage', TRANSCRIBE_NUMBERS_STORAGE_KEY) === 'true'
    rememberTranscribeNumbers()
    const [fileResult, sourceResult] = await Promise.allSettled([
      api.files(),
      (async () => {
        const savedJobId = readStored('sessionStorage', JOB_STORAGE_KEY)
        const recovered = savedJobId ? await recoverJob(savedJobId) : false
        if (!recovered) await readClipboard()
      })(),
    ])
    if (fileResult.status === 'fulfilled') files.value = fileResult.value
    else sourceError.value = friendlyError(fileResult.reason)
    if (sourceResult.status === 'rejected') pageError.value = friendlyError(sourceResult.reason)
  } catch (error) {
    pageError.value = friendlyError(error)
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  stopInfoxPolling()
  publishing.dispose()
  window.clearInterval(editorTabTimer)
  editorNavigation.reset()
  window.removeEventListener('beforeunload', warnUnsaved)
  document.removeEventListener('click', dismissMyassetSettings, true)
  disposed = true
  stopPolling()
})

async function refreshClipboard() {
  if (actionBusy.value || isRunning.value) return
  actionBusy.value = true
  sourceError.value = ''
  try {
    await readClipboard()
    announcement.value = '클립보드 원문을 새로 불러왔습니다.'
  } catch (error) {
    sourceError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}

async function refreshFiles() {
  if (actionBusy.value || isRunning.value) return
  actionBusy.value = true
  sourceError.value = ''
  try {
    const refreshed = await api.files()
    files.value = refreshed
    if (!refreshed.some((file) => file.path === selectedFile.value)) selectedFile.value = ''
    announcement.value = '파일 목록을 새로 불러왔습니다.'
  } catch (error) {
    sourceError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}

async function runJob() {
  if (!canRun.value) return
  sourceError.value = ''
  pageError.value = ''
  actionBusy.value = true
  try {
    await flushCompressed()
    const source = sourceMode.value === 'clipboard'
      ? { html: clipboardText.value }
      : { file_path: selectedFile.value }
    await submitJob(source, selectedModel.value, transcribeNumbers.value)
  } catch (error) {
    pageError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}

function dismissMyassetSettings(event: MouseEvent) {
  if (!myassetSettingsOpen.value || !(event.target instanceof Node)) return
  if (myassetSettingsPanel.value?.contains(event.target) || myassetSettingsButton.value?.contains(event.target)) return
  myassetSettingsOpen.value = false
}

async function closeMyassetSettings() {
  myassetSettingsOpen.value = false
  await nextTick()
  myassetSettingsButton.value?.focus()
}

async function loadMyasset() {
  if (inputBusy.value || !selectedModel.value || myassetSettingsError.value) return
  const baseDate = myassetDate.value
  const gubun = Number(myassetGubun.value)
  const model = selectedModel.value
  const transcribeNumbersForJob = transcribeNumbers.value
  actionBusy.value = true
  fetchingMyasset.value = true
  sourceError.value = ''
  try {
    await flushCompressed()
    const source = await api.myassetSource(baseDate, gubun)
    if (disposed) return
    sourceMode.value = 'clipboard'
    clipboardFormat.value = 'html'
    clipboardText.value = source.html
    announcement.value = 'myasset 원문을 불러왔습니다. 변환 및 압축을 시작합니다.'
    await nextTick()
    if (disposed) return
    pageError.value = ''
    await submitJob({ html: source.html }, model, transcribeNumbersForJob)
  } catch (error) {
    if (!disposed) sourceError.value = friendlyError(error)
  } finally {
    fetchingMyasset.value = false
    actionBusy.value = false
  }
}

async function submitJob(source: { html: string } | { file_path: string }, model: string,
                         transcribeNumbersForJob: boolean): Promise<void> {
  try {
    const created = await api.createJob({ ...source, model, transcribe_numbers: transcribeNumbersForJob })
    if (disposed) return
    rememberJob(created)
    schedulePoll()
  } catch (error) {
    if (disposed) return
    if (error instanceof ApiError && error.status === 409 && error.jobId) {
      try {
        const activeJob = await api.job(error.jobId)
        if (disposed) return
        rememberJob(activeJob)
        schedulePoll()
        pageError.value = '이미 진행 중인 작업을 이어서 표시합니다.'
        return
      } catch (recoveryError) {
        pageError.value = friendlyError(recoveryError)
        return
      }
    }
    sourceError.value = friendlyError(error)
  }
}

async function retryJob() {
  if (!job.value || actionBusy.value || loading.value || isRunning.value || !selectedModel.value) return
  actionBusy.value = true
  pageError.value = ''
  try {
    await flushCompressed()
    const retried = await api.retry(job.value.id, selectedModel.value)
    rememberJob(retried)
    schedulePoll()
  } catch (error) {
    pageError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}

async function copyResult(stage: 'serialized' | 'compressed') {
  if (!job.value || actionBusy.value) return
  actionBusy.value = true
  pageError.value = ''
  try {
    if (stage === 'compressed') await flushCompressed()
    await api.copy(job.value.id, stage)
    announcement.value = stage === 'serialized' ? '직렬화 원문을 클립보드에 복사했습니다.' : '압축 결과를 클립보드에 복사했습니다.'
  } catch (error) {
    pageError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}

async function downloadResult(stage: 'serialized' | 'compressed') {
  if (!job.value || actionBusy.value) return
  actionBusy.value = true
  pageError.value = ''
  const name = stage === 'serialized' ? 'serialized.txt' : 'compressed.txt'
  try {
    if (stage === 'compressed') await flushCompressed()
    const blob = await api.artifact(job.value.id, name)
    const href = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = href
    const dateParts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Seoul', year: '2-digit', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date(job.value.created_at))
    const date = ['year', 'month', 'day'].map((part) => dateParts.find((item) => item.type === part)!.value).join('')
    const downloadName = `${date}_${stage === 'serialized' ? '원본데이터' : '대본'}.txt`
    link.download = downloadName
    link.click()
    URL.revokeObjectURL(href)
    announcement.value = `${downloadName} 다운로드를 시작했습니다.`
  } catch (error) {
    pageError.value = friendlyError(error)
  } finally {
    actionBusy.value = false
  }
}
// ---- InfoX step display model (display only; execution guards stay in handlers) ----
const infoxStartBlocker = computed(() => {
  if (infoxLoading.value) return '보관된 실행 기록을 확인하는 중입니다.'
  if (isRunning.value) return '대본 생성이 끝난 뒤 시작할 수 있습니다.'
  if (composingCompressed.value) return '한글 입력을 완료한 뒤 시작할 수 있습니다.'
  if (!compressedDraft.value.trim()) return '대본이 비어 있어 시작할 수 없습니다.'
  if (!infoxTitle.value.trim()) return '영상 제목을 입력하세요.'
  if (infoxNeedsReview.value && !infoxReviewConfirmed.value) return '검토 사유를 확인하고 사용 여부에 체크하세요.'
  return ''
})
// A changed script may start a new run only when the current run is not being tracked.
const infoxRestartable = computed(() => !!infoxRun.value && infoxChanged.value && !runTracking(infoxRun.value) &&
  infoxRun.value.stage !== 'prepared' && !infoxRun.value.requires_reconciliation)
const infoxStepList = computed(() => infoxSteps({ run: infoxRun.value, pending: !!infoxPending.value,
  publication: publishing.record.value, preparingPublication: publishing.preparing.value }))
const infoxStep = computed(() => Object.fromEntries(infoxStepList.value.map(step => [step.key, step])) as Record<InfoxStepKey, InfoxStep>)
const infoxCurrent = computed(() => currentStep(infoxStepList.value))
const infoxAssets = computed(() => assetSummary(infoxRun.value))
const infoxProgress = computed(() => infoxCurrent.value.key === 'produce' ? productionProgress(infoxRun.value)
  : ['publish', 'complete'].includes(infoxCurrent.value.key) ? publicationProgress(publishing.record.value) : null)

interface InfoxAction { key: string; label: string; disabled: boolean; run: () => void }

// Manual production actions, in priority order. The first is the primary action.
const infoxManualActions = computed<InfoxAction[]>(() => {
  const run = infoxRun.value
  if (!run || infoxPending.value) return []
  if (run.review_mode === 'manual_review') return []
  const locked = inputBusy.value || infoxLoading.value || infoxStarting.value
  const tracked = locked || !!run.tracking_active
  const unsaved = !run.asset_save || run.asset_save.state === 'not_started'
  const actions: InfoxAction[] = []
  if (['prepared', 'needs_attention', 'project_created'].includes(run.stage) && (!run.asset_audit || run.asset_audit.state === 'not_started')) {
    actions.push({ key: 'start', label: run.stage === 'prepared' ? '확정본으로 제작 계속' : '기존 제작 상태 확인', disabled: locked, run: startInfox })
  }
  if (run.continuation === 'automatic' && run.stage === 'needs_attention' && run.asset_audit?.state !== 'not_started' && run.asset_save?.state === 'not_started') {
    actions.push({ key: 'continue', label: '기존 자동 실행 계속 확인', disabled: tracked, run: startInfox })
  }
  if (['ready', 'not_needed'].includes(run.replacement_plan?.state ?? '') && !['saved_verified', 'unchanged_verified'].includes(run.asset_save?.state ?? '')) {
    actions.push({ key: 'save', disabled: tracked, run: saveInfox,
      label: !unsaved ? '기존 저장·검증 상태 다시 확인' : run.replacement_plan?.state === 'not_needed' ? '교체 불필요 상태 서버 재확인' : '검증한 교체 계획 서버 저장' })
  }
  if (unsaved && run.asset_audit?.state === 'completed' && run.snapshot.settings.auto_replace && !run.requires_reconciliation &&
      (['assets_audited', 'replacement_planned', 'needs_attention'].includes(run.stage) || (run.stage === 'selecting_favorites' && !run.tracking_active))) {
    actions.push({ key: 'plan', disabled: tracked, run: planInfox,
      label: !run.replacement_plan || run.replacement_plan.state === 'not_started' ? '즐겨찾기 교체 계획 만들기' : '고정한 교체 계획 다시 확인' })
  }
  if (unsaved && run.tracking?.completion_seen && !run.requires_reconciliation &&
      (['draft_ready', 'assets_audited', 'replacement_planned', 'needs_attention'].includes(run.stage) || (run.stage === 'auditing_assets' && !run.tracking_active))) {
    actions.push({ key: 'audit', disabled: tracked, run: auditInfox,
      label: !run.asset_audit || run.asset_audit.state === 'not_started' ? '기업 에셋 검사' : '배정 에셋 다시 검사' })
  }
  return actions
})

const publishStartBlocker = computed(() => {
  if (publishing.loading.value) return '게시 기록과 이용량을 확인하는 중입니다.'
  if (publishing.busy.value || publishing.uncertain.value) return '이전 게시 요청의 결과를 확인하는 중입니다. 같은 요청을 다시 보내지 않습니다.'
  if (!formatPublishMetadata(publishing.marketDate.value)) return '올바른 게시 기준일을 선택하세요.'
  const charge = publishing.charge.value
  if (charge?.blocked_reason) return publishErrors[charge.blocked_reason] ?? '게시 조건을 확인하지 못했습니다. 다시 준비하세요.'
  if (!charge?.available) return '렌더 이용량을 확인하지 못해 시작할 수 없습니다. 화면을 새로고침해 다시 확인하세요.'
  if (!charge.can_start) return '게시 시작 조건을 충족하지 못했습니다.'
  if (!publishing.reviewConfirmed.value) return 'InfoX에서 검토·수정 후 저장했는지 확인란에 체크하세요.'
  return ''
})

type PrimaryAction = InfoxAction & { hint: string; busyLabel?: string }
const infoxPrimary = computed<PrimaryAction | null>(() => {
  const run = infoxRun.value
  if (infoxPending.value) {
    return { key: 'recheck', label: '확정 결과 다시 확인', busyLabel: '확정 결과 확인 중…', hint: '',
      disabled: inputBusy.value || infoxLoading.value || composingCompressed.value, run: prepareInfox }
  }
  if (!run) {
    return { key: 'begin', label: 'InfoX 영상 제작 시작', busyLabel: '제작을 준비하는 중…', hint: infoxStartBlocker.value,
      disabled: !!infoxStartBlocker.value || inputBusy.value || infoxStarting.value, run: beginInfoxProduction }
  }
  if (manualReviewAvailable(run) && run.review_mode === 'automatic') {
    return { key: 'review', label: 'InfoX에서 검토·수정', hint: '',
      disabled: infoxStarting.value || editorBusy.value, run: () => { void reopenEditor() } }
  }
  const manual = infoxManualActions.value[0]
  if (manual) {
    return { ...manual, busyLabel: '요청 확인 중…',
      hint: manual.disabled && run.tracking_active ? '진행 중인 추적이 끝난 뒤 사용할 수 있습니다.' : '' }
  }
  if (!infoxEditorReady.value) return null
  const record = publishing.record.value
  if (!record || publishing.preparing.value) {
    const hint = publishStartBlocker.value
    return { key: 'publish', label: '편집 완료 · 렌더 및 유튜브 업로드', hint, disabled: !publishing.canStart.value || !!hint,
      run: () => { void publishing.start() } }
  }
  if (record.stage === 'clipboard_failed') {
    return { key: 'copy', label: '링크 다시 복사', hint: '', disabled: publishing.busy.value || publishing.loading.value,
      run: () => { void publishing.copy() } }
  }
  return null
})
const infoxSecondary = computed(() => infoxManualActions.value.slice(1))
const infoxPrimaryBusy = computed(() => {
  const key = infoxPrimary.value?.key ?? ''
  if (key === 'recheck') return actionBusy.value
  if (key === 'publish' || key === 'copy') return publishing.busy.value
  return infoxStarting.value
})

const infoxHeadline = computed(() => {
  const run = infoxRun.value
  if (infoxPending.value) return '이전 확정 요청 확인 필요'
  if (!run) return infoxStarting.value ? '대본 저장·전송본 확정 중' : '전송본 확정 전'
  if (infoxCurrent.value.key === 'produce') return infoxStageLabels[run.stage] ?? run.stage
  const record = publishing.record.value
  if (!record || publishing.preparing.value) return publishing.loading.value ? '게시 기록 확인 중' : '편집 후 게시 대기'
  if (record.stage === 'completed') return '게시 완료 · 링크 확인 가능'
  if (record.stage === 'clipboard_failed') return '업로드 완료 · 링크 복사 필요'
  if (record.stage === 'failed') return '게시가 중단되었습니다'
  if (record.stage === 'needs_attention') return '게시 상태 확인이 필요합니다'
  return '게시 진행 중'
})

const infoxNext = computed(() => {
  const run = infoxRun.value
  const step = infoxCurrent.value
  if (infoxPending.value) return '이전 요청의 확정 결과부터 확인합니다. 추가 편집은 기존 전송본과 분리됩니다.'
  if (!run) return '제목과 설정을 확인한 뒤 시작하세요. 최신 대본 저장, 전송본 확정, 제작 요청을 차례로 실행합니다.'
  if (run.stage === 'prepared' && !run.requires_reconciliation) return '확정 상태만 보관되어 있습니다. 확정본으로 제작 계속을 눌러야 제작을 시작합니다.'
  if (step.key === 'produce') {
    if (run.requires_reconciliation) return '자동 제작 단계에서 접수 여부를 확인하세요. 같은 요청을 다시 보내지 않습니다.'
    if (step.status === 'active') return '자동으로 진행 중입니다. 결과가 확인되면 다음 단계가 표시됩니다.'
    if (step.status === 'failed') return '제작 실패가 확인되었습니다. 자동 제작 단계에서 원인을 확인하세요.'
    if (infoxManualActions.value.length) return '아래 버튼으로 다음 처리를 진행하세요.'
    return '자동 제작 단계에서 원인을 확인한 뒤 진행 상태를 다시 조회하세요.'
  }
  const record = publishing.record.value
  if (step.key === 'publish') {
    if (!record || publishing.preparing.value) return 'InfoX에서 에셋·장면·자막·음성을 검토하고 필요한 항목을 교체해 저장한 뒤, 검토 완료를 확인하고 게시하세요.'
    if (step.status === 'active') return '렌더와 업로드를 자동으로 진행합니다. 실행 중에는 편집을 계속하지 마세요.'
    return '원인을 확인한 뒤 필요하면 새 회차 렌더·업로드를 준비하세요.'
  }
  if (record?.stage === 'clipboard_failed') return '업로드와 링크는 확인되었습니다. 링크 복사만 다시 시도하세요.'
  return '검증된 영상 링크를 확인하세요.'
})

const infoxSummaries = computed<Partial<Record<InfoxStepKey, string>>>(() => {
  const run = infoxRun.value
  const assets = infoxAssets.value
  const record = publishing.record.value
  return {
    prepare: run ? `전송본 v${run.snapshot.revision} · ${run.snapshot.scenes.length}장면 · ${run.snapshot.settings.title}` : undefined,
    produce: assets ? `기업명 검출 ${assets.detected} · 확인 불가 ${assets.unknown} · 교체 ${assets.replacements ?? '-'}${assets.saveLabel ? ` · ${assets.saveLabel}` : ''}` : undefined,
    publish: record ? `${record.attempt_number ?? 1}회차 · 기준일 ${record.snapshot.market_date} · ${publishStages[record.stage] ?? '상태 확인 필요'}` : undefined,
    complete: record?.uploaded_verified && record.share_url ? record.share_url : undefined,
  }
})
const infoxEditorStatusVisible = computed(() => !!editorAttempt.value || !!editorNavigation.error.value)
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#main-content" aria-label="Market Brief 홈">
        <span class="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <span>
          <strong>MARKET BRIEF</strong>
          <small>LOCAL EDITING DESK</small>
        </span>
      </a>
      <div v-if="session" class="system-status" aria-label="변환 설정">
        <label class="transcribe-toggle" for="transcribe-numbers">
          <input id="transcribe-numbers" v-model="transcribeNumbers" type="checkbox"
            :disabled="loading || actionBusy || isRunning" @change="rememberTranscribeNumbers" />
          <span>수치 한국어로 바로 전사</span>
        </label>
        <span class="divider" aria-hidden="true" />
        <label for="start-model" class="sr-only">시작 모델</label>
        <select id="start-model" v-model="selectedModel" class="model-select"
          :disabled="loading || actionBusy || isRunning" @change="rememberModel">
          <option v-for="model in session.models" :key="model.id" :value="model.id">{{ model.label }}</option>
        </select>
        <span class="keys">{{ providerLabel }} 키 {{ configuredKeys.join(' · ') || '없음' }}</span>
      </div>
    </header>

    <main id="main-content">
      <section class="hero">
        <p class="eyebrow">DAILY MARKET · VIDEO SCRIPT</p>
        <h1>영상 원고 만들기</h1>
        <p class="hero-copy">시장 요약 원문을 가져오면, 다섯 장면의 간결한 영상 문장으로 정리합니다.</p>
      </section>

      <div v-if="pageError" class="notice notice-error" role="alert">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5m0 3.5v.5M10.3 4.4 3.1 17a2 2 0 0 0 1.7 3h14.4a2 2 0 0 0 1.7-3L13.7 4.4a2 2 0 0 0-3.4 0Z" /></svg>
        <span>{{ pageError }}</span>
      </div>
      <p class="sr-only" aria-live="polite">{{ announcement }}</p>

      <div class="workspace-grid">
        <section class="source-card" aria-labelledby="source-heading">
          <div class="section-heading source-heading">
            <span class="step-number">01</span>
            <div>
              <p class="eyebrow">SOURCE</p>
              <h2 id="source-heading">원문 선택</h2>
            </div>
            <div class="myasset-actions">
              <button class="ghost-button myasset-load" type="button"
                :disabled="inputBusy || !selectedModel || !!myassetSettingsError" @click="loadMyasset">
                {{ fetchingMyasset ? 'myasset에서 불러오는 중…' : 'myasset에서 불러오기' }}
              </button>
              <div class="myasset-settings" @keydown.esc.stop.prevent="closeMyassetSettings">
                <button ref="myassetSettingsButton" class="ghost-button" type="button"
                  :disabled="inputBusy" :aria-expanded="myassetSettingsOpen" aria-controls="myasset-settings-panel"
                  @click="myassetSettingsOpen = !myassetSettingsOpen">불러오기 설정</button>
                <div v-if="myassetSettingsOpen" ref="myassetSettingsPanel" id="myasset-settings-panel" class="myasset-settings-panel"
                  role="group" aria-label="myasset 불러오기 설정">
                  <label for="myasset-date">날짜</label>
                  <input id="myasset-date" v-model="myassetDate" type="date" :disabled="inputBusy" />
                  <label for="myasset-gubun">gubun</label>
                  <input id="myasset-gubun" v-model="myassetGubun" type="number" min="0" step="1" :disabled="inputBusy" />
                  <p class="field-note">선택한 원문을 불러온 뒤 변환 및 압축을 자동 시작합니다.</p>
                </div>
              </div>
            </div>
          </div>
          <p v-if="myassetSettingsError" class="inline-error" role="alert">{{ myassetSettingsError }}</p>

          <fieldset class="source-tabs" :disabled="inputBusy">
            <legend class="sr-only">원문 가져올 곳</legend>
            <label :class="{ selected: sourceMode === 'clipboard' }">
              <input v-model="sourceMode" type="radio" value="clipboard" />
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5h6m-7 2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-2M9 3h6v4H9V3Z" /></svg>
              클립보드
            </label>
            <label :class="{ selected: sourceMode === 'file' }">
              <input v-model="sourceMode" type="radio" value="file" />
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h5l2 3h5a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" /></svg>
              저장된 파일
            </label>
          </fieldset>

          <div v-if="sourceMode === 'clipboard'" class="source-input">
            <div class="label-row">
              <label for="clipboard-source">클립보드 HTML 원문</label>
              <div class="field-actions">
                <span class="format-chip">{{ clipboardFormat === 'html' ? 'HTML' : 'TEXT' }}</span>
                <button type="button" :disabled="loading || actionBusy || isRunning" @click="refreshClipboard">
                  클립보드 다시 읽기
                </button>
              </div>
            </div>
            <textarea
              id="clipboard-source"
              v-model="clipboardText"
              :disabled="inputBusy"
              placeholder="클립보드의 시장 요약이 여기에 표시됩니다. 직접 붙여넣거나 수정할 수도 있습니다."
              spellcheck="false"
            />
            <p class="field-note">표시된 시장 원문을 확인하고 필요한 부분을 다듬어 주세요.</p>
          </div>

          <div v-else class="source-input">
            <div class="label-row">
              <label for="file-source">HTML 파일 선택</label>
              <div class="field-actions">
                <button type="button" :disabled="loading || actionBusy || isRunning" @click="refreshFiles">
                  파일 목록 새로고침
                </button>
              </div>
            </div>
            <div class="select-wrap">
              <select id="file-source" v-model="selectedFile" :disabled="inputBusy || files.length === 0">
                <option value="" disabled>{{ files.length === 0 ? '사용할 수 있는 파일이 없습니다' : '파일을 선택하세요' }}</option>
                <option v-for="file in files" :key="file.path" :value="file.path">
                  {{ file.path }} · {{ fileSize(file.size) }}
                </option>
              </select>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 10 4 4 4-4" /></svg>
            </div>
            <p class="field-note">프로젝트의 src 폴더 안에 있는 파일만 선택할 수 있습니다.</p>
          </div>

          <div v-if="sourceError" class="inline-error" role="alert">{{ sourceError }}</div>

          <button class="primary-button" type="button" :disabled="!canRun" @click="runJob">
            <span>{{ isRunning ? '압축하는 중…' : actionBusy ? '요청하는 중…' : '변환 및 1분 압축' }}</span>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>
          </button>
        </section>

        <aside class="progress-column" aria-label="작업 진행 상황">
          <StatusTimeline v-if="job" :job="job" :models="session?.models ?? []" />
          <section v-else class="empty-status">
            <div class="orb" aria-hidden="true"><span /><span /><span /></div>
            <p class="eyebrow">READY WHEN YOU ARE</p>
            <h2>{{ loading ? '작업 공간을 여는 중' : '원문을 기다리고 있습니다' }}</h2>
            <p>왼쪽에서 원문을 확인한 뒤 압축을 시작하세요. 진행 과정이 이곳에 차례로 기록됩니다.</p>
          </section>

          <div v-if="job?.state === 'failed'" class="failure-card" role="alert">
            <p v-if="manuallyEdited">아래 안내는 생성 당시 기록입니다. 수정한 대본은 자동 재검증하지 않습니다.</p>
            <strong>작업 중 문제가 생겼습니다</strong>
            <p>{{ job.error || '작업을 완료하지 못했습니다.' }}</p>
            <button v-if="job.serialized" type="button" :disabled="loading || actionBusy" @click="retryJob">압축 다시 시도</button>
          </div>
          <div v-if="job?.state === 'needs_review'" class="failure-card review-card" role="alert">
            <p v-if="manuallyEdited">아래 안내는 생성 당시 기록입니다. 수정한 대본은 자동 재검증하지 않습니다.</p>
            <strong>생성된 대본을 확인하세요</strong>
            <p>자동 보정이 멈췄습니다. 표시된 사유를 확인하고 필요한 부분을 직접 수정하세요.</p>
            <ul>
              <li v-for="(issue, index) in job.validation_issues ?? []" :key="index">{{ issue }}</li>
            </ul>
            <button v-if="job.serialized && job.compressed !== null" type="button" :disabled="loading || actionBusy" @click="retryJob">대본 재생성</button>
          </div>
        </aside>
      </div>

      <section v-if="job && (job.serialized || job.compressed !== null)" class="results" aria-labelledby="results-heading">
        <header class="results-heading">
          <div>
            <p class="eyebrow">OUTPUT</p>
            <h2 id="results-heading">작업 결과</h2>
          </div>
          <div v-if="job.state === 'completed'" class="result-stats">
            <span v-if="job.scene_count !== null">입력 {{ job.scene_count }}개 → {{ manuallyEdited ? '수정한 대본' : '최종 5줄 대본' }}</span>
            <span v-if="job.body_char_count !== null">본문 {{ (manuallyEdited ? draftCharCount : job.body_char_count).toLocaleString('ko-KR') }}자</span>
            <span>{{ job.elapsed_seconds.toFixed(1) }}초 소요</span>
          </div>
        </header>

        <ul v-if="job.warnings.length" class="warning-list" aria-label="검토 안내">
          <li v-if="manuallyEdited">생성 당시 검토 기록이며 수정한 대본의 검증 결과가 아닙니다.</li>
          <li v-for="warning in job.warnings" :key="warning">{{ warning }}</li>
        </ul>

        <div class="result-grid">
          <ResultPanel
            v-if="job.serialized"
            title="정돈된 시장 원문"
            eyebrow="SERIALIZED"
            :value="job.serialized"
            label="직렬화 원문"
            copy-label="직렬화 원문 복사"
            download-label="직렬화 원문 다운로드"
            :busy="actionBusy"
            @copy="copyResult('serialized')"
            @download="downloadResult('serialized')"
          />
          <ResultPanel
            v-if="job.compressed !== null"
            :title="manuallyEdited ? '수정한 영상 대본' : job.state === 'needs_review' ? '생성된 대본 · 검토 필요' : job.state === 'failed' ? '검증 실패 대본' : '최종 5줄 영상 대본'"
            eyebrow="COMPRESSED"
            :value="compressedDraft"
            :body-char-count="manuallyEdited ? draftCharCount : job.body_char_count ?? undefined"
            :excess-char-count="manuallyEdited ? Math.max(0, draftCharCount - 550) : job.excess_char_count"
            :editable="!loading && !isRunning"
            :save-error="compressedSaveError"
            label="압축 결과"
            copy-label="압축 결과 복사"
            download-label="압축 결과 다운로드"
            :busy="actionBusy"
            @edit="editCompressed"
            @composition="composeCompressed"
            @retry-save="retryCompressedSave"
            @copy="copyResult('compressed')"
            @download="downloadResult('compressed')"
          />
        </div>
        <section v-if="job.compressed !== null" class="infox-preparation" aria-labelledby="infox-heading">
          <header class="infox-heading">
            <p class="eyebrow">INFOX</p>
            <h3 id="infox-heading">InfoX 영상 제작</h3>
            <ol class="infox-stepper" aria-label="InfoX 진행 단계">
              <li v-for="step in infoxStepList" :key="step.key" :data-status="step.status"
                :aria-current="step.key === infoxCurrent.key ? 'step' : undefined">
                <span aria-hidden="true">{{ step.number }}</span>{{ step.title }}<span class="sr-only">{{ ' · ' + stepStatusLabels[step.status] }}</span>
              </li>
            </ol>
          </header>
          <div class="infox-layout">
            <aside class="infox-panel" aria-label="InfoX 현재 상태와 다음 행동">
              <p class="infox-panel-step">
                <span>{{ infoxCurrent.number }}단계 · {{ infoxCurrent.title }}</span>
                <span class="infox-badge" :data-status="infoxCurrent.status">{{ stepStatusLabels[infoxCurrent.status] }}</span>
              </p>
              <p class="infox-panel-headline" role="status">{{ infoxHeadline }}</p>
              <InfoXProgress v-if="infoxProgress" :progress="infoxProgress"
                :name="infoxCurrent.key === 'produce' ? '현재 제작 작업 진행률' : '현재 게시 작업 진행률'" />
              <p class="infox-panel-next">{{ infoxNext }}</p>
              <p v-if="infoxChanged" class="infox-panel-note">현재 편집본 또는 설정이 확정본과 다릅니다.</p>
              <p v-if="infoxChanged && infoxRun" class="infox-panel-note">진행 중인 실행은 확정본 v{{ infoxRun.snapshot.revision }}을 그대로 사용합니다.</p>
              <div v-if="infoxError" class="inline-error" role="alert">{{ infoxError }}</div>
              <template v-if="infoxPrimary">
                <button class="primary-button infox-primary" type="button" :disabled="infoxPrimary.disabled || infoxPrimaryBusy"
                  :aria-describedby="infoxPrimary.hint ? 'infox-primary-hint' : undefined" @click="infoxPrimary.run()">
                  <span>{{ infoxPrimaryBusy && infoxPrimary.busyLabel ? infoxPrimary.busyLabel : infoxPrimary.label }}</span>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>
                </button>
                <p v-if="infoxPrimary.hint" id="infox-primary-hint" class="infox-hint">{{ infoxPrimary.hint }}</p>
              </template>
              <div class="infox-panel-secondary">
                <button v-if="infoxRun && infoxCurrent.key === 'produce' && runTracking(infoxRun)" type="button" class="ghost-button"
                  @click="pollInfox(infoxRun.id)">진행 상태 다시 조회</button>
                <a v-if="infoxRun && editorEligible(infoxRun) && !editorBusy && editorUrl(infoxRun)" class="ghost-button" :href="editorUrl(infoxRun)!" target="_blank" rel="noopener noreferrer">InfoX 편집 화면 수동 열기</a>
              </div>
              <p v-if="infoxEditorStatusVisible" class="infox-panel-editor" role="status">편집기 탭: {{ editorMessage }}</p>
            </aside>

            <div class="infox-steps">
              <InfoXStep :step="infoxStep.prepare" :current="infoxCurrent.key === 'prepare'" :summary="infoxSummaries.prepare"
                :force-open="infoxRestartable">
                <div class="infox-fields">
                  <label for="infox-title">InfoX 영상 제목</label>
                  <input id="infox-title" v-model="infoxTitle" type="text" maxlength="200" :disabled="inputBusy || infoxLoading || !!infoxPending" />
                  <details class="infox-title-settings">
                    <summary>기본 제목 설정</summary>
                    <div class="infox-title-settings-fields">
                      <label for="infox-title-template">기본 제목 형식</label>
                      <input id="infox-title-template" v-model="infoxTitleTemplateDraft" type="text" maxlength="200" aria-describedby="infox-title-template-help" :disabled="inputBusy || infoxLoading || !!infoxPending" />
                      <p id="infox-title-template-help">MMDD는 한국 시간의 오늘 월일로 바뀝니다. 기본 형식은 이 브라우저에 저장됩니다.</p>
                      <p v-if="infoxTitleTemplateValid">미리보기: {{ formatInfoxTitle(infoxTitleTemplateDraft) }}</p>
                      <p v-else>비어 있지 않은 200자 이하의 제목 형식을 입력하세요.</p>
                      <button type="button" class="ghost-button" :disabled="!infoxTitleTemplateValid || inputBusy || infoxLoading || !!infoxPending" @click="saveInfoxTitleTemplate">기본값 저장 및 현재 제목에 적용</button>
                      <p v-if="infoxTitleTemplateError" class="inline-error" role="alert">{{ infoxTitleTemplateError }}</p>
                      <p v-if="infoxTitleTemplateSaved" role="status">기본 제목을 저장하고 현재 제목에 적용했습니다.</p>
                    </div>
                  </details>
                  <p class="infox-settings">제작 설정: 템플릿 1786 · 남/아나운서/당당 · 속도 1.0 · 목표 60초</p>
                  <label class="transcribe-toggle">
                    <input v-model="infoxAutoReplace" type="checkbox" :disabled="inputBusy || infoxLoading || !!infoxPending" />
                    <span>반도체 즐겨찾기 자동 대체 사용</span>
                  </label>
                  <label v-if="infoxNeedsReview" class="transcribe-toggle">
                    <input v-model="infoxReviewConfirmed" type="checkbox" :disabled="inputBusy || infoxLoading || !!infoxPending" />
                    <span>검토 사유를 확인하고 이 대본을 사용합니다</span>
                  </label>
                </div>
                <div v-if="infoxRun" class="infox-confirmed">
                  <p><strong>{{ infoxRun.stage === 'prepared' ? '전송본이 확정되었습니다.' : '확정본으로 실행한 기록입니다.' }}</strong></p>
                  <p>확정 대본 버전 {{ infoxRun.snapshot.revision }} · {{ infoxRun.snapshot.scenes.length }}장면 · {{ infoxRun.snapshot.settings.title }}</p>
                  <p>자동 대체 {{ infoxRun.snapshot.settings.auto_replace ? '사용' : '사용 안 함' }}</p>
                  <template v-if="infoxRestartable">
                    <p>수정본으로 제작하면 새 실행을 만듭니다. 기존 실행과 영상은 변경하지 않습니다.</p>
                    <button type="button" class="ghost-button" :disabled="!!infoxStartBlocker || inputBusy || infoxStarting"
                      @click="beginInfoxProduction">수정본으로 새 제작 시작</button>
                    <p v-if="infoxStartBlocker" class="infox-hint">{{ infoxStartBlocker }}</p>
                  </template>
                </div>
              </InfoXStep>

              <InfoXStep :step="infoxStep.produce" :current="infoxCurrent.key === 'produce'" :summary="infoxSummaries.produce">
                <template v-if="infoxRun">
                  <p v-if="infoxRun.tracking?.error_code" class="inline-error">{{ infoxErrorLabels[infoxRun.tracking.error_code] ?? '기존 실행을 확인하세요.' }}</p>
                  <p v-if="infoxRun.requires_reconciliation" class="inline-error">접수 여부 확인이 필요합니다. 기존 실행은 다시 전송하지 않습니다.</p>
                  <p v-if="infoxRun.tracking?.progress != null && infoxActiveStages.has(infoxRun.stage) && !infoxRun.tracking.completion_seen">완료 판정은 작업 이벤트와 결과 검증을 기다립니다.</p>
                  <ul v-if="infoxAssets" class="infox-counts" aria-label="에셋 검사·교체 요약">
                    <li><strong>{{ infoxAssets.detected }}</strong>기업명 검출</li>
                    <li><strong>{{ infoxAssets.unknown }}</strong>확인 불가</li>
                    <li><strong>{{ infoxAssets.replacements ?? '-' }}</strong>교체 대상</li>
                    <li><strong>{{ infoxAssets.notDetected + infoxAssets.exempt }}</strong>미검출·예외</li>
                  </ul>
                  <p v-if="infoxAssets && !infoxAssets.audited">배정 에셋 검사 중입니다.</p>
                  <p v-if="infoxAssets?.unknown">확인 불가 {{ infoxAssets.unknown }}개는 교체 대상에서 제외하고 진행을 막지 않습니다.</p>
                  <p v-if="!infoxRun.snapshot.settings.auto_replace">이 전송본은 자동 대체를 사용하지 않습니다.</p>
                  <InfoXAssetSave v-if="infoxRun.asset_save" :save="infoxRun.asset_save" />
                  <div v-if="infoxSecondary.length" class="infox-actions">
                    <button v-for="action in infoxSecondary" :key="action.key" type="button" class="ghost-button"
                      :disabled="action.disabled" @click="action.run()">{{ action.label }}</button>
                  </div>
                  <details v-if="infoxRun.asset_audit?.state && infoxRun.asset_audit.state !== 'not_started' || infoxRun.replacement_plan?.state && infoxRun.replacement_plan.state !== 'not_started'" class="infox-disclosure">
                    <summary>장면별 검사·교체 근거</summary>
                    <InfoXAssetAudit v-if="infoxRun.asset_audit" :audit="infoxRun.asset_audit" :save-started="!!infoxRun.asset_save && infoxRun.asset_save.state !== 'not_started'" />
                    <InfoXReplacementPlan v-if="infoxRun.replacement_plan" :plan="infoxRun.replacement_plan" :save-started="!!infoxRun.asset_save && infoxRun.asset_save.state !== 'not_started'" />
                  </details>
                  <details class="infox-disclosure">
                    <summary>처리 기록과 식별 정보</summary>
                    <p v-if="infoxRun.video_id">영상 ID: {{ infoxRun.video_id }} · 작업 ID: {{ infoxRun.task_id ?? '접수 확인 전' }}</p>
                    <p v-else>영상 ID 확보 전입니다.</p>
                    <p>실행 기록 버전 {{ infoxRun.version }} · 진행 방식 {{ infoxRun.continuation === 'automatic' ? '자동' : '수동' }}</p>
                    <p v-if="infoxRun.tracking">작업 상태 {{ infoxRun.tracking.task_status }}<template v-if="infoxRun.tracking.reconnects"> · 재연결 {{ infoxRun.tracking.reconnects }}회</template></p>
                    <p v-if="infoxAssets?.saveLabel && infoxRun.asset_save?.verified_version">검증한 서버 버전: {{ infoxRun.asset_save.verified_version }}</p>
                  </details>
                </template>
              </InfoXStep>

              <InfoXStep :step="infoxStep.publish" :current="infoxCurrent.key === 'publish'" :summary="infoxSummaries.publish"
                :force-open="!!infoxRun?.video_id && !!infoxRun?.tracking_active">
                <template v-if="infoxRun?.video_id">
                  <section class="infox-editor" aria-label="편집기 브라우저 이동">
                    <h5>InfoX 편집기</h5>
                    <p>에셋·장면·자막·음성을 직접 검토하고 필요한 항목을 교체한 뒤 InfoX에서 저장하세요. 2단계 자동 검증 경고가 있어도 수기 검토 후 게시할 수 있습니다.</p>
                    <p v-if="infoxRun.review_blocked_reason">{{ infoxRun.review_blocked_reason === 'request_unconfirmed' ? '기존 요청의 접수 결과를 먼저 확인하세요.' : infoxRun.review_blocked_reason === 'publication_active' ? '렌더·업로드 작업이 끝난 뒤 편집할 수 있습니다.' : '제작·저장 작업이 끝나면 수기 검토로 전환할 수 있습니다.' }}</p>
                    <div class="infox-actions">
                      <button v-if="manualReviewAvailable(infoxRun)" type="button" class="ghost-button" :disabled="editorBusy || infoxStarting" @click="reopenEditor">{{ infoxRun.review_mode === 'automatic' ? '수기 검토로 전환하고 편집기 열기' : '편집기 다시 열기' }}</button>
                      <template v-if="editorAttempt && ['navigation_requested', 'login_required', 'ready_reported'].includes(editorAttempt.state)">
                        <button type="button" class="ghost-button" :disabled="editorBusy" @click="editorNavigation.report('login_required')">로그인 필요 상태 기록</button>
                        <button type="button" class="ghost-button" :disabled="editorBusy" @click="editorNavigation.report('failed')">이동 실패 상태 기록</button>
                        <button type="button" class="ghost-button" :disabled="editorBusy" @click="editorNavigation.report('ready_reported')">편집기 준비 사용자 확인</button>
                      </template>
                    </div>
                    <p v-if="!infoxEditorStatusVisible">브라우저 이동 기록이 없습니다. 편집기 다시 열기 또는 수동 링크를 사용하세요.</p>
                  </section>
                  <InfoXPublishing :run="infoxRun" :control="publishing" />
                  <!-- The current attempt's files appear in exactly one step, so polling and saved intents are never doubled. -->
                  <InfoXDownloads v-if="publishing.record.value && infoxStep.complete.status === 'waiting'" :key="publishing.record.value.id"
                    :publication-id="publishing.record.value.id" :uploaded-verified="publishing.record.value.uploaded_verified" />
                  <div v-if="['failed', 'needs_attention'].includes(publishing.record.value?.stage ?? '') && !publishing.preparing.value" class="infox-actions">
                    <button type="button" class="ghost-button" :disabled="!publishing.canPrepare.value || !infoxEditorReady" @click="publishing.prepareNew()">새 회차 렌더·업로드 준비</button>
                  </div>
                </template>
              </InfoXStep>

              <InfoXStep :step="infoxStep.complete" :current="infoxCurrent.key === 'complete'" :summary="infoxSummaries.complete">
                <template v-if="publishing.record.value?.uploaded_verified">
                  <p>InfoX의 해당 업로드 작업 결과와 일부공개 기록을 확인했습니다.</p>
                  <p v-if="publishing.record.value.share_url" class="infox-link">
                    <a :href="publishing.record.value.share_url" target="_blank" rel="noopener noreferrer">{{ publishing.record.value.share_url }}</a>
                  </p>
                  <p v-if="publishing.record.value.stage === 'completed'">링크를 클립보드에 복사했습니다.</p>
                  <p v-else-if="publishing.record.value.stage === 'clipboard_failed'" class="inline-error">링크 복사에 실패했습니다. 업로드 결과와 링크는 보존되어 있습니다.</p>
                  <InfoXDownloads v-if="infoxStep.complete.status !== 'waiting'" :key="publishing.record.value.id"
                    :publication-id="publishing.record.value.id" :uploaded-verified="publishing.record.value.uploaded_verified" />
                  <div v-if="!publishing.preparing.value" class="infox-actions">
                    <button type="button" class="ghost-button" :disabled="!publishing.canPrepare.value || !infoxEditorReady" @click="publishing.prepareNew()">새 회차 렌더·업로드 준비</button>
                  </div>
                </template>
              </InfoXStep>
            </div>
          </div>
        </section>
      </section>
    </main>

    <footer class="footer-note">
      <span>LOCAL ONLY</span>
      <p>이 도구는 현재 컴퓨터에서만 열리며 결과는 실행별 폴더에 저장됩니다.</p>
    </footer>
  </div>
</template>
