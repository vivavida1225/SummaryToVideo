<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { api, ApiError, bootstrapSession } from './api'
import ResultPanel from './components/ResultPanel.vue'
import StatusTimeline from './components/StatusTimeline.vue'
import type { Job, SessionInfo, SourceFile } from './types'

const JOB_STORAGE_KEY = 'market-compressor.job-id'
const MODEL_STORAGE_KEY = 'market-compressor.model'
const TERMINAL_STATES = new Set(['completed', 'failed', 'needs_review'])

const session = ref<SessionInfo | null>(null)
const selectedModel = ref('')
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

function rememberModel() {
  writeStored('localStorage', MODEL_STORAGE_KEY, selectedModel.value)
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
  compressedDraft.value = text
  if (!composingCompressed.value) void flushCompressed().catch(() => {})
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
  window.addEventListener('beforeunload', warnUnsaved)
  document.addEventListener('click', dismissMyassetSettings, true)
  try {
    session.value = await bootstrapSession()
    const savedModel = readStored('localStorage', MODEL_STORAGE_KEY)
    selectedModel.value = session.value.models.some(model => model.id === savedModel) ? savedModel! : session.value.model
    rememberModel()
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
    await submitJob(source, selectedModel.value)
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
    await submitJob({ html: source.html }, model)
  } catch (error) {
    if (!disposed) sourceError.value = friendlyError(error)
  } finally {
    fetchingMyasset.value = false
    actionBusy.value = false
  }
}

async function submitJob(source: { html: string } | { file_path: string }, model: string): Promise<void> {
  try {
    const created = await api.createJob({ ...source, model })
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
      <div v-if="session" class="system-status" aria-label="연결 정보">
        <span class="online-dot" aria-hidden="true" />
        <span>로컬 연결</span>
        <span class="divider" aria-hidden="true" />
        <label for="start-model" class="sr-only">시작 모델</label>
        <select id="start-model" v-model="selectedModel" class="model-select"
          :disabled="loading || actionBusy || isRunning" @change="rememberModel">
          <option v-for="model in session.models" :key="model.id" :value="model.id">{{ model.label }}</option>
        </select>
        <span class="keys">키 {{ session.configured_keys.join(' · ') || '없음' }}</span>
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
            <strong>생성된 대본의 분량을 확인하세요</strong>
            <p>자동 보정을 멈췄습니다. 대본을 확인한 뒤 필요한 경우 재생성을 눌러 주세요.</p>
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
            :title="manuallyEdited ? '수정한 영상 대본' : job.state === 'needs_review' ? '생성된 대본 · 분량 확인 필요' : job.state === 'failed' ? '검증 실패 대본' : '최종 5줄 영상 대본'"
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
      </section>
    </main>

    <footer class="footer-note">
      <span>LOCAL ONLY</span>
      <p>이 도구는 현재 컴퓨터에서만 열리며 결과는 실행별 폴더에 저장됩니다.</p>
    </footer>
  </div>
</template>
