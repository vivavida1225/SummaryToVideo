import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'
import type { Job } from './types'

// Flow tests for the unified "InfoX 영상 제작 시작" button and the step layout.
// All network, clipboard and InfoX calls are replaced by a local fetch mock.

const session = { token: 'session-token', model: 'gpt-6.1-sol', configured_keys: [1],
  configured_keys_by_provider: { gemini: [1], openai: [1] },
  models: [{ id: 'gpt-6.1-sol', label: 'GPT 6.1 Sol', provider: 'openai' }] }
const RUN_ID = 'a'.repeat(32)

function makeJob(overrides: Partial<Job> = {}): Job {
  return { id: 'job-1', parent_id: null, state: 'completed', source: 'clipboard', created_at: '2026-11-09T00:00:00Z',
    elapsed_seconds: 1, attempt: 1, max_attempts: 3, key_number: null, retry_at: null, serialized: '원문',
    compressed: '원본', revision: 0, scene_count: 1, body_char_count: 2, warnings: [], error: null,
    output_dir: 'outputs/job-1', events: [], ...overrides }
}
function makeRun(overrides: Record<string, unknown> = {}) {
  return { id: RUN_ID, stage: 'prepared', version: 0, requires_reconciliation: false,
    snapshot: { job_id: 'job-1', revision: 0, text: '원본', text_sha256: '', scenes: [{ line_number: 1, scene_index: 0, text: '원본' }],
      settings: { title: '시황 영상', template_id: 1786, voice_id: 'v', voice_speed: '1.0', auto_replace: true } },
    video_id: null, task_id: null, ...overrides }
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
function path(input: RequestInfo | URL) {
  return typeof input === 'string' ? input : input instanceof URL ? input.pathname : new URL(input.url).pathname
}

type Handler = (path: string, init: RequestInit) => Response | Promise<Response>
function install(handler: Handler) {
  const posts: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const p = path(input)
    if (p === '/api/session') return json(session)
    if (p === '/api/files') return json({ files: [] })
    if (init.method === 'POST') posts.push(p)
    return handler(p, init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, posts }
}
function fakeTab() {
  return { closed: false, opener: {}, document: { title: '', body: { textContent: '' } },
    location: { replace: vi.fn() }, close: vi.fn(function (this: { closed: boolean }) { this.closed = true }) }
}
const claimed = (body: string) => json({ claimed: true, attempt: { id: JSON.parse(body).attempt_id, run_id: RUN_ID, version: 0,
  state: 'waiting', mode: 'automatic', video_id: null, editor_url: null, editor_status: 'unconfirmed' } })

it('2단계 경고를 보존하고 수기 전환 후 확인한 최신 영상으로 게시한다', async () => {
  sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem('market-compressor.job-id', 'job-1')
  const tab = fakeTab()
  vi.spyOn(window, 'open').mockReturnValue(tab as never)
  let current: Record<string, unknown> = makeRun({ stage:'needs_attention', version:7, review_mode:'automatic', video_id:'video-1',
    task_id:'task-1', tracking_active:false, can_enter_manual_review:true, can_open_editor:false,
    can_prepare_publication:false, tracking:{completion_seen:true,task_status:'completed',progress:100},
    replacement_plan:{state:'needs_attention',error_code:'source_changed',selections:[]} })
  let posted: Record<string, unknown> | undefined
  const { posts } = install((p, init) => {
    if(p === '/api/jobs/job-1') return json(makeJob())
    if(p === '/api/jobs/job-1/infox-runs') return json({run:current})
    if(p.endsWith('/manual-review')) {
      current = {...current, review_mode:'manual_review', version:8,can_open_editor:true,can_prepare_publication:true}
      return json(current)
    }
    if(p.endsWith('/editor-attempts')) return init.method==='POST' ? claimed(String(init.body)) : json({attempt:null})
    if(p.includes('/editor-attempts/')) {
      const update=JSON.parse(String(init.body))
      return json({id:p.split('/').at(-1),run_id:RUN_ID,version:update.expected_version+1,state:update.state,
        video_id:'video-1',editor_url:'https://infox2.infomaxai.com/studio/library/videos/video-1?chatboxView=script'})
    }
    if(p.endsWith('/publications/history')) return json({publications:[]})
    if(p.endsWith('/publications/preview')) return json({available:true,charged:true,can_start:true,
      blocked_reason:null,latest_publication_id:null,has_remote_render:false,remote_updated_at:'2026-11-12T06:43:40Z'})
    if(p.endsWith('/publications')) {
      if(init.method!=='POST') return json({publication:null})
      posted=JSON.parse(String(init.body))
      return json({id:'pub-new',run_id:RUN_ID,video_id:'video-1',attempt_number:1,stage:'rendering',progress:42,
        snapshot:{market_date:'2026-11-12',title:'title',description:'description'},uploaded_verified:false})
    }
    if(p.endsWith('/downloads')) return json({downloads:[]})
    throw new Error(`unexpected ${p}`)
  })
  const view=render(App)
  try {
    const panel=await screen.findByRole('complementary',{name:'InfoX 현재 상태와 다음 행동'})
    const open=await within(panel).findByRole('button',{name:'InfoX에서 검토·수정'})
    expect(within(panel).queryByRole('link',{name:'InfoX 편집 화면 수동 열기'})).not.toBeInTheDocument()
    expect(screen.getByRole('region',{name:'InfoX 수기 검토·수정·게시'})).toHaveAttribute('data-current','true')
    await fireEvent.click(open)
    await waitFor(()=>expect(tab.location.replace).toHaveBeenCalled())
    expect(await within(panel).findByRole('link',{name:'InfoX 편집 화면 수동 열기'})).toBeVisible()
    const publish=await within(panel).findByRole('button',{name:'편집 완료 · 렌더 및 유튜브 업로드'})
    expect(publish).toBeDisabled()
    await fireEvent.click(screen.getByRole('checkbox',{name:'InfoX에서 검토·수정 후 저장했으며, 현재 영상으로 게시합니다'}))
    await waitFor(()=>expect(publish).toBeEnabled())
    await fireEvent.click(publish)
    await waitFor(()=>expect(posted).toMatchObject({review_confirmed:true,reviewed_remote_updated_at:'2026-11-12T06:43:40Z'}))
    expect(screen.getByRole('region',{name:'자동 제작·검사'})).toHaveAttribute('data-status','attention')
    expect(posts.some(p=>p.endsWith('/start') || p.endsWith('/save-assets'))).toBe(false)
  } finally { view.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() }
})

describe('InfoX 통합 제작 시작', { timeout: 15000 }, () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem('market-compressor.job-id', 'job-1') })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('한 번의 클릭으로 최신 대본 저장·확정·대기 탭 연결·제작 요청을 순서대로 실행하고 중복 클릭을 막는다', async () => {
    const tab = fakeTab()
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as never)
    let save!: (response: Response) => void
    let prepared: Record<string, unknown> | undefined
    const { posts } = install((p, init) => {
      if (p.endsWith('/compressed')) return new Promise(resolve => { save = resolve })
      if (p === '/api/jobs/job-1/infox-runs') {
        if (init.method !== 'POST') return json({ run: null })
        prepared = JSON.parse(String(init.body))
        return json(makeRun({ snapshot: { ...makeRun().snapshot, revision: 1, text: '최신 편집' } }), 201)
      }
      if (p.endsWith('/editor-attempts')) return init.method === 'POST' ? claimed(String(init.body)) : json({ attempt: null })
      if (p.endsWith('/start')) return json(makeRun({ stage: 'creating_project', tracking_active: true }), 202)
      if (p === `/api/infox-runs/${RUN_ID}`) return json(makeRun({ stage: 'generating', tracking_active: true, video_id: 'video-1',
        task_id: 'task-1', tracking: { completion_seen: false, task_status: 'running', progress: 42 } }))
      if (p === '/api/jobs/job-1') return json(makeJob())
      throw new Error(`unexpected ${p}`)
    })
    render(App)
    const editor = await screen.findByRole('textbox', { name: '압축 결과' })
    await waitFor(() => expect(editor).not.toHaveAttribute('readonly'))
    await fireEvent.update(editor, '최신 편집')
    const button = await screen.findByRole('button', { name: 'InfoX 영상 제작 시작' })
    await waitFor(() => expect(button).toBeEnabled())
    await fireEvent.click(button)
    await fireEvent.click(button)
    // The waiting tab is reserved synchronously in the click, before any request completes.
    expect(open).toHaveBeenCalledTimes(1)
    expect(posts.filter(p => p.endsWith('/infox-runs'))).toHaveLength(0)
    save(json(makeJob({ compressed: '최신 편집', revision: 1, edited_at: 'now' })))
    await waitFor(() => expect(posts.filter(p => p.endsWith('/start'))).toHaveLength(1))
    expect(prepared).toMatchObject({ revision: 1, new_run: false, settings: { auto_replace: true } })
    expect(posts).toEqual(['/api/jobs/job-1/compressed', '/api/jobs/job-1/infox-runs',
      `/api/infox-runs/${RUN_ID}/editor-attempts`, `/api/infox-runs/${RUN_ID}/start`])
    expect(tab.close).not.toHaveBeenCalled()
    expect(open).toHaveBeenCalledTimes(1)
    const panel = screen.getByRole('complementary', { name: 'InfoX 현재 상태와 다음 행동' })
    await waitFor(() => expect(within(panel).getByRole('progressbar', { name: '현재 제작 작업 진행률' })).toHaveAttribute('value', '42'), { timeout: 4000 })
    expect(within(panel).getByText('시안 제작 중 · 42%')).toBeInTheDocument()
    expect(sessionStorage.getItem('market-compressor.infox-pending.job-1')).toBe(null)
  })

  it.each([
    ['save_failure', 503],
    ['revision_conflict', 409],
  ])('%s이면 확정·제작을 하지 않고 이번 클릭의 대기 탭만 닫는다', async (_mode, status) => {
    const tab = fakeTab()
    vi.spyOn(window, 'open').mockReturnValue(tab as never)
    const { posts } = install((p, init) => {
      if (p.endsWith('/compressed')) return json({ detail: '저장 거절' }, status)
      if (p === '/api/jobs/job-1/infox-runs') return json({ run: null })
      if (p === '/api/jobs/job-1') return json(makeJob())
      throw new Error(`unexpected ${p} ${init.method}`)
    })
    render(App)
    const editor = await screen.findByRole('textbox', { name: '압축 결과' })
    await waitFor(() => expect(editor).not.toHaveAttribute('readonly'))
    await fireEvent.update(editor, '수정 중')
    await fireEvent.click(screen.getByRole('button', { name: 'InfoX 영상 제작 시작' }))
    await waitFor(() => expect(tab.close).toHaveBeenCalledTimes(1))
    expect(posts.filter(p => p.endsWith('/infox-runs') || p.endsWith('/start'))).toHaveLength(0)
    expect(editor).toHaveValue('수정 중')
  })

  it('확정 응답이 유실되면 제작을 시작하지 않고 같은 요청의 결과만 확인한 뒤 명시적 계속을 기다린다', async () => {
    const tab = fakeTab()
    vi.spyOn(window, 'open').mockReturnValue(tab as never)
    const bodies: string[] = []
    const { posts } = install((p, init) => {
      if (p === '/api/jobs/job-1/infox-runs') {
        if (init.method !== 'POST') return json({ run: null })
        bodies.push(String(init.body))
        if (bodies.length === 1) throw new TypeError('network disconnected')
        return json(makeRun())
      }
      if (p.endsWith('/editor-attempts')) return init.method === 'POST' ? claimed(String(init.body)) : json({ attempt: null })
      if (p === '/api/jobs/job-1') return json(makeJob())
      throw new Error(`unexpected ${p}`)
    })
    render(App)
    const button = await screen.findByRole('button', { name: 'InfoX 영상 제작 시작' })
    await waitFor(() => expect(button).toBeEnabled())
    await fireEvent.click(button)
    const recheck = await screen.findByRole('button', { name: '확정 결과 다시 확인' })
    expect(tab.close).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toHaveTextContent('제작은 시작하지 않았습니다')
    await waitFor(() => expect(recheck).toBeEnabled())
    await fireEvent.click(recheck)
    expect(await screen.findByRole('button', { name: '확정본으로 제작 계속' })).toBeEnabled()
    expect(bodies[1]).toBe(bodies[0])
    expect(posts.some(p => p.endsWith('/start'))).toBe(false)
  })

  it('같은 전송본의 진행 중 실행이 반환되면 다시 시작하지 않고 기존 상태를 표시한다', async () => {
    const tab = fakeTab()
    vi.spyOn(window, 'open').mockReturnValue(tab as never)
    const active = makeRun({ stage: 'generating', tracking_active: true, video_id: 'video-1', task_id: 'task-1',
      tracking: { completion_seen: false, task_status: 'running', progress: null } })
    const { posts } = install((p, init) => {
      if (p === '/api/jobs/job-1/infox-runs') return init.method === 'POST' ? json(active) : json({ run: null })
      if (p === `/api/infox-runs/${RUN_ID}`) return json(active)
      if (p.endsWith('/editor-attempts')) return json({ attempt: null })
      if (p.endsWith('/publications')) return json({ publication: null })
      if (p.endsWith('/publications/history')) return json({ publications: [] })
      if (p === '/api/jobs/job-1') return json(makeJob())
      throw new Error(`unexpected ${p}`)
    })
    render(App)
    const button = await screen.findByRole('button', { name: 'InfoX 영상 제작 시작' })
    await waitFor(() => expect(button).toBeEnabled())
    await fireEvent.click(button)
    expect(await screen.findByText('시안 제작 중 · 현재 작업의 완료 신호 대기')).toBeInTheDocument()
    expect(screen.getByText('시안 제작 중 · 진행률 확인 중')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(tab.close).toHaveBeenCalledTimes(1)
    expect(posts.some(p => p.endsWith('/start'))).toBe(false)
  })

  it('검토 확인 전에는 시작 버튼 옆에 활성화 조건을 표시한다', async () => {
    install((p) => {
      if (p === '/api/jobs/job-1/infox-runs') return json({ run: null })
      if (p === '/api/jobs/job-1') return json(makeJob({ state: 'needs_review', validation_issues: ['한자 포함'] }))
      throw new Error(`unexpected ${p}`)
    })
    render(App)
    const button = await screen.findByRole('button', { name: 'InfoX 영상 제작 시작' })
    await waitFor(() => expect(button).toHaveAccessibleDescription('검토 사유를 확인하고 사용 여부에 체크하세요.'))
    expect(button).toBeDisabled()
    await fireEvent.click(screen.getByRole('checkbox', { name: '검토 사유를 확인하고 이 대본을 사용합니다' }))
    await waitFor(() => expect(button).toBeEnabled())
  })
})

describe('InfoX 단계형 화면', { timeout: 15000 }, () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem('market-compressor.job-id', 'job-1') })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  const ready = makeRun({ stage: 'ready_for_editor', video_id: 'video-1', task_id: 'task-1', tracking_active: false,
    tracking: { completion_seen: true, task_status: 'completed', progress: 100 },
    asset_audit: { state: 'completed', issues: [], items: [
      { scene_index: 0, layer_id: 'm', item_index: null, verdict: 'detected', reason: 'company_detected',
        reference: { kind: 'image' }, hits: [{ term: '삼성전자', field: 'tags[0]', source: 's' }], metadata_sources: [] },
      { scene_index: 1, layer_id: 'm', item_index: null, verdict: 'unknown', reason: 'metadata_missing',
        reference: { kind: 'image' }, hits: [], metadata_sources: [] }] },
    replacement_plan: { state: 'ready', selections: [] },
    asset_save: { state: 'saved_verified', receipt_state: 'accepted', verified_version: 'v2' } })
  const publication = (overrides: Record<string, unknown> = {}) => ({ id: 'pub-1', run_id: RUN_ID, video_id: 'video-1',
    attempt_number: 1, previous_publication_id: null, created_at: '2026-11-09T00:00:00Z', version: 0, stage: 'completed',
    failed_stage: null, error_code: null, progress: null,
    snapshot: { market_date: '2026-11-09', title: '261109 AI 영상 시황', description: '2026년 11월 9일 AI 영상 시황', privacy_status: 'unlisted' },
    uploaded_verified: true, youtube_video_id: 'abcdefghijk', share_url: 'https://youtu.be/abcdefghijk', clipboard_state: 'copied', ...overrides })

  function installReady(latest: unknown, preview = { available: true, charged: true, can_start: true, blocked_reason: null,
    latest_publication_id: null, has_remote_render: false, remote_updated_at: '2026-11-04T00:00:00Z' }, downloads: unknown[] = []) {
    return install((p, init) => {
      if (p === '/api/infox-publications/pub-1/downloads') return json({ downloads })
      if (p === '/api/jobs/job-1/infox-runs') return json({ run: ready })
      if (p.endsWith('/editor-attempts')) return json({ attempt: null })
      if (p.endsWith('/publications/history')) return json({ publications: latest ? [latest] : [] })
      if (p.endsWith('/publications/preview')) return json(preview)
      if (p === `/api/infox-runs/${RUN_ID}/publications`) {
        if (init.method === 'POST') return json(publication({ stage: 'rendering', progress: 42, uploaded_verified: false, share_url: null }), 202)
        return json({ publication: latest })
      }
      if (p === '/api/infox-publications/pub-1') return json(latest ?? publication({ stage: 'rendering', progress: 42, uploaded_verified: false, share_url: null }))
      if (p === '/api/jobs/job-1') return json(makeJob())
      throw new Error(`unexpected ${p}`)
    })
  }

  it('완료 데이터는 이전 단계를 한 줄 요약으로 접고 검증된 링크와 다음 행동을 바로 보여준다', async () => {
    installReady(publication())
    render(App)
    const link = await screen.findByRole('link', { name: 'https://youtu.be/abcdefghijk' })
    expect(link).toBeVisible()
    const toggles = screen.getAllByRole('button', { name: /^펼치기/ })
    expect(toggles.map(button => button.textContent?.trim())).toEqual(['펼치기 제작 준비', '펼치기 자동 제작·검사', '펼치기 InfoX 수기 검토·수정·게시'])
    expect(screen.getByText('기업명 검출 1 · 확인 불가 1 · 교체 0 · 교체 저장·검증 완료')).toBeVisible()
    // Detailed per-scene evidence stays folded even after the step is expanded.
    expect(screen.getByText('삼성전자 · tags[0]')).not.toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '펼치기 자동 제작·검사' }))
    expect(screen.getByRole('button', { name: '접기 자동 제작·검사' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('삼성전자 · tags[0]')).not.toBeVisible()
    await fireEvent.click(screen.getByText('장면별 검사·교체 근거'))
    expect(screen.getByText('삼성전자 · tags[0]')).toBeVisible()
    expect(screen.getByRole('button', { name: '새 회차 렌더·업로드 준비' })).toBeVisible()
  })

  it('편집 단계의 주요 버튼은 오른쪽 상태 영역에 하나만 있고 게시를 한 번만 요청한다', async () => {
    const { posts } = installReady(null)
    render(App)
    const panel = await screen.findByRole('complementary', { name: 'InfoX 현재 상태와 다음 행동' })
    const button = await within(panel).findByRole('button', { name: '편집 완료 · 렌더 및 유튜브 업로드' })
    expect(button).toBeDisabled()
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'InfoX에서 검토·수정 후 저장했으며, 현재 영상으로 게시합니다' })).toBeEnabled())
    await fireEvent.click(screen.getByRole('checkbox', { name: 'InfoX에서 검토·수정 후 저장했으며, 현재 영상으로 게시합니다' }))
    await waitFor(() => expect(button).toBeEnabled())
    expect(screen.getAllByRole('button', { name: '편집 완료 · 렌더 및 유튜브 업로드' })).toHaveLength(1)
    expect(screen.getByLabelText('InfoX 영상 제목')).not.toBeVisible()
    await fireEvent.click(button)
    await fireEvent.click(button)
    await waitFor(() => expect(within(panel).getByRole('progressbar', { name: '현재 게시 작업 진행률' })).toHaveAttribute('value', '42'))
    expect(posts.filter(p => p.endsWith('/publications'))).toHaveLength(1)
  })

  it('업로드 후 링크 복사만 실패하면 완료 단계를 유지하고 링크 다시 복사만 제공한다', async () => {
    const { posts } = installReady(publication({ stage: 'clipboard_failed', clipboard_state: 'failed' }))
    render(App)
    const panel = await screen.findByRole('complementary', { name: 'InfoX 현재 상태와 다음 행동' })
    expect(await within(panel).findByRole('button', { name: '링크 다시 복사' })).toBeEnabled()
    expect(screen.getByRole('link', { name: 'https://youtu.be/abcdefghijk' })).toBeVisible()
    expect(screen.getByText('링크 복사에 실패했습니다. 업로드 결과와 링크는 보존되어 있습니다.')).toBeVisible()
    expect(posts).toHaveLength(0)
  })

  it('렌더 이용량 조회가 막히면 비활성 사유를 버튼 가까이 표시한다', async () => {
    installReady(null, { available: true, charged: null, can_start: false, blocked_reason: 'publication_active',
      latest_publication_id: null, has_remote_render: false } as never)
    render(App)
    const panel = await screen.findByRole('complementary', { name: 'InfoX 현재 상태와 다음 행동' })
    const button = await within(panel).findByRole('button', { name: '편집 완료 · 렌더 및 유튜브 업로드' })
    await waitFor(() => expect(button).toHaveAccessibleDescription('이 영상에 진행 중인 작업이 있습니다. 완료 후 다시 준비하세요.'))
    expect(button).toBeDisabled()
  })
  const download = (overrides: Record<string, unknown> = {}) => ({ id: 'download-1', publication_id: 'pub-1', kind: 'all', automatic: false,
    state: 'partial', error_code: null, items: [
      { kind: 'script', filename: '261109_대본.txt', state: 'completed', path: 'C:/Downloads/261109_대본.txt', size: 10, sha256: 'h', error_code: null },
      { kind: 'video', filename: '261109_영상시황.mp4', state: 'failed', path: null, size: null, sha256: null, error_code: 'timeout' }], ...overrides })

  it('완료 단계에 현재 회차의 파일 저장 결과와 실패 파일 재다운로드를 한 곳에만 표시한다', async () => {
    const { posts } = installReady(publication(), undefined, [download()])
    render(App)
    expect(await screen.findByText('일부 다운로드 완료')).toBeVisible()
    expect(screen.getAllByRole('region', { name: '파일 다운로드', hidden: true })).toHaveLength(1)
    expect(screen.getByText('261109_영상시황.mp4 · 저장 실패')).toBeVisible()
    expect(screen.getByRole('button', { name: '실패 파일 다시 다운로드' })).toBeVisible()
    expect(screen.getByRole('button', { name: '대본·원본·영상 3개 파일 다운로드' })).toBeVisible()
    expect(posts).toHaveLength(0)
  })

  it('게시가 끝나기 전에는 자동 저장 영상 결과를 편집 및 게시 단계에서 보여준다', async () => {
    const rendering = publication({ stage: 'uploading', progress: 10, uploaded_verified: false, share_url: null })
    installReady(rendering, undefined, [download({ kind: 'video', automatic: true, state: 'completed', items: [
      { kind: 'video', filename: '261109_영상시황.mp4', state: 'completed', path: 'C:/Downloads/261109_영상시황.mp4', size: 99, sha256: 'h', error_code: null }] })])
    render(App)
    expect(await screen.findByText('렌더 완료 영상 자동 다운로드')).toBeVisible()
    expect(screen.getAllByRole('region', { name: '파일 다운로드', hidden: true })).toHaveLength(1)
    expect(screen.getByRole('region', { name: 'InfoX 수기 검토·수정·게시' })).toContainElement(screen.getByRole('region', { name: '파일 다운로드' }))
    expect(screen.queryByRole('button', { name: '대본·원본·영상 3개 파일 다운로드' })).not.toBeInTheDocument()
  })
})
