import { computed, ref } from 'vue'
import type { EditorAttempt, InfoXRun } from './types'
import type { api } from './api'

export function editorUrl(run: InfoXRun): string | null {
  return run.video_id && /^[A-Za-z0-9_-]{1,128}$/.test(run.video_id)
    ? `https://infox2.infomaxai.com/studio/library/videos/${run.video_id}?chatboxView=script` : null
}
export function editorEligible(run: InfoXRun): boolean {
  if (run.can_open_editor !== undefined) return run.can_open_editor
  return run.stage === 'ready_for_editor' && ['saved_verified', 'unchanged_verified'].includes(run.asset_save?.state ?? '')
    && run.requires_reconciliation === false && editorUrl(run) !== null
}

export function manualReviewAvailable(run: InfoXRun): boolean {
  return run.can_enter_manual_review ?? editorEligible(run)
}

type Port = Pick<typeof api, 'latestEditor' | 'claimEditor' | 'updateEditor'>
const labels: Record<string, string> = {
  waiting: '대기 탭 확보 · 서버 검증이 끝나기 전에는 편집기로 이동하지 않습니다.',
  navigate_intent: '브라우저 이동 의도를 기록했습니다. 이동 요청 결과를 기다립니다.',
  blocked: '팝업이 차단되었습니다. 팝업을 허용한 뒤 편집기 다시 열기를 누르세요.',
  closed: '대기 탭이 닫혔습니다. 검증 완료 뒤 편집기 다시 열기를 누르세요.',
  failed: '브라우저 이동 요청이 실패했습니다. 편집기 다시 열기 또는 수동 링크를 사용하세요.',
  unknown: '브라우저 이동 결과가 불명확합니다. 열린 탭을 먼저 확인하고 필요하면 명시적으로 다시 여세요.',
  navigation_requested: '브라우저에 편집 URL 이동을 요청했습니다. 로그인·편집기 준비·재생은 자동 확인할 수 없습니다.',
  login_required: '사용자가 로그인 필요 상태를 기록했습니다. 열린 InfoX 탭에서 로그인한 뒤 다시 확인하세요.',
  ready_reported: '사용자가 편집기 준비를 확인했습니다. 자동 로그인·재생 검증 결과와는 별도입니다.',
}

export class EditorNavigation {
  readonly attempt = ref<EditorAttempt | null>(null)
  readonly error = ref('')
  readonly busy = ref(false)
  readonly navigating = ref(false)
  readonly restored = ref(false)
  readonly message = computed(() => this.error.value || (this.restored.value &&
    ['waiting', 'navigate_intent'].includes(this.attempt.value?.state ?? '') ? labels.unknown :
    labels[this.attempt.value?.state ?? ''] ?? '브라우저 이동 기록이 없습니다. 기존 실행 복원은 조회만 수행합니다.'))
  private tab: Window | null = null
  // A tab opened inside a click before the run exists; undefined = no reservation.
  private reserved: Window | null | undefined = undefined
  private owner: string | null = null
  private runId: string | null = null
  private epoch = 0

  constructor(private port: Port, private open: () => Window | null = () => window.open('about:blank', '_blank')) {}

  reset() {
    this.epoch++
    this.release()
    if (this.owner && this.tab && !this.tab.closed) this.tab.close()
    this.tab = null; this.owner = null; this.runId = null
    this.attempt.value = null; this.error.value = ''; this.restored.value = false
  }

  async restore(id: string) {
    const epoch = this.epoch
    try {
      const result = await this.port.latestEditor(id)
      if (epoch !== this.epoch || this.owner) return
      this.attempt.value = result.attempt; this.restored.value = true
    } catch { if (epoch === this.epoch) this.error.value = '이동 기록 조회 실패 · 열린 탭을 먼저 확인하세요. 자동으로 다시 열지 않습니다.' }
  }

  /** Opens the waiting tab synchronously in a click, before the run id is known. */
  reserve(): boolean {
    if (this.busy.value || this.navigating.value || this.owner || this.reserved !== undefined) return false
    this.reserved = this.openWaiting()
    return true
  }

  /** Closes only a reserved tab that was never attached to a run. */
  release() {
    const tab = this.reserved
    this.reserved = undefined
    if (tab && !tab.closed) tab.close()
  }

  private openWaiting(): Window | null {
    const tab = this.open()
    if (tab) {
      tab.opener = null
      tab.document.title = 'InfoX 검증 대기'
      tab.document.body.textContent = 'InfoX 서버 검증 대기 중입니다. 검증 완료 뒤 같은 실행의 편집기로 이동합니다. 이 탭을 닫으면 원래 앱에서 다시 열 수 있습니다.'
    }
    return tab
  }

  async begin(id: string, mode: 'automatic' | 'manual') {
    if (this.busy.value || this.navigating.value || this.owner) { this.release(); return }
    this.epoch++ // Invalidate an earlier restore before consuming ownership.
    this.busy.value = true; this.error.value = ''; this.restored.value = false
    const epoch = this.epoch
    const attemptId = crypto.randomUUID().replaceAll('-', '')
    let tab: Window | null = null
    try {
      // Without a reservation this runs before the first await, inside the click activation.
      const reserved = this.reserved
      this.reserved = undefined
      // A reserved tab closed meanwhile is reported as closed by checkClosed().
      tab = reserved !== undefined ? reserved : this.openWaiting()
      const result = await this.port.claimEditor(id, { attempt_id: attemptId, mode, tab_state: tab ? 'created' : 'blocked' })
      if (epoch !== this.epoch) { tab?.close(); return }
      this.attempt.value = result.attempt
      if (!result.claimed) { tab?.close(); this.restored.value = true; return }
      if (tab) { this.tab = tab; this.owner = attemptId; this.runId = id }
    } catch {
      tab?.close()
      if (epoch === this.epoch) this.error.value = labels.unknown!
    } finally { this.busy.value = false }
  }

  async report(state: string) {
    const attempt = this.attempt.value
    if (!attempt || this.busy.value) return
    this.busy.value = true
    const epoch = this.epoch
    try {
      const result = await this.port.updateEditor(attempt.run_id, attempt.id, { expected_version: attempt.version, state })
      if (epoch !== this.epoch || this.attempt.value?.id !== attempt.id) return
      this.attempt.value = result
      this.error.value = ''
    } catch { if (epoch === this.epoch) this.error.value = labels.unknown! }
    finally { this.busy.value = false }
  }

  async checkClosed() {
    if (this.owner && this.tab?.closed && !this.navigating.value) {
      this.owner = null
      await this.report('closed')
    }
  }

  async advance(run: InfoXRun) {
    const attempt = this.attempt.value
    if (!this.owner || this.runId !== run.id || !attempt || this.navigating.value) return
    if (this.tab?.closed) {
      await this.checkClosed(); return
    }
    if (!editorEligible(run)) return
    this.navigating.value = true
    // Consume local ownership BEFORE any await. No poll or rerender can replay.
    this.owner = null
    const tab = this.tab
    const epoch = this.epoch
    try {
      const intended = await this.port.updateEditor(run.id, attempt.id, { expected_version: attempt.version, state: 'navigate_intent' })
      if (epoch !== this.epoch) { tab?.close(); return }
      this.attempt.value = intended
      if (!tab || tab.closed) { await this.report('closed'); return }
      if (intended.video_id !== run.video_id || intended.editor_url !== editorUrl(run)) throw new Error('target mismatch')
      try { tab.location.replace(editorUrl(run)!) }
      catch { await this.report('failed'); return }
      await this.report('navigation_requested')
    } catch { if (epoch === this.epoch) this.error.value = labels.unknown! }
    finally { this.navigating.value = false }
  }
}
