import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'
import type { Job } from './types'

const session = {
  token: 'session-token', model: 'gemini-3.8-flash', configured_keys: [1, 3],
  models: [
    { id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash' },
    { id: 'gemini-3.7-flash', label: 'Gemini 3.7 Flash' },
    { id: 'gemini-3.6-flash', label: 'Gemini 3.6 Flash' },
    { id: 'gemini-3.5-flash-lite', label: 'Gemini 3.5 Flash-Lite' },
  ],
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    parent_id: null,
    state: 'queued',
    source: 'clipboard',
    created_at: '2026-09-11T00:00:00Z',
    elapsed_seconds: 0,
    attempt: 0,
    max_attempts: 3,
    key_number: null,
    retry_at: null,
    serialized: null,
    compressed: null,
    scene_count: null,
    body_char_count: null,
    warnings: [],
    error: null,
    output_dir: 'outputs/job-1',
    events: [{ at: '2026-09-11T00:00:00Z', message: '작업 대기 중' }],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? input : input instanceof URL ? input.pathname : new URL(input.url).pathname
}

function requestInit(input: RequestInfo | URL, init?: RequestInit): RequestInit {
  return input instanceof Request ? { method: input.method, headers: input.headers, body: input.body } : (init ?? {})
}

function installBootstrapFetch(extra: (path: string, init: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input)
    if (path === '/api/session') return jsonResponse(session)
    if (path === '/api/files') return jsonResponse({ files: [{ path: '오늘 시장.html', size: 2048 }] })
    if (path === '/api/clipboard/read') return jsonResponse({ text: '<article>오늘의 시장</article>', format: 'html' })
    return extra(path, requestInit(input, init))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function finishBootstrap() {
  await screen.findByRole('heading', { name: '영상 원고 만들기' })
  await waitFor(() => expect(screen.getByRole('button', { name: '변환 및 1분 압축' })).toBeEnabled())
}

describe('App', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.useRealTimers()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('초과 대본을 복원하고 명시적 재생성에서만 요청한다', async () => {
    sessionStorage.setItem('market-compressor.job-id', 'job-1')
    const review = makeJob({ state: 'needs_review', serialized: '보관된 원문', compressed: '가'.repeat(551),
      body_char_count: 551, excess_char_count: 1, validation_issues: ['1자 초과', '지수 불일치'] })
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs/job-1') return jsonResponse(review)
      if (path === '/api/jobs/job-1/retry') return jsonResponse(makeJob({ id: 'regenerated' }), 202)
      if (path === '/api/jobs/job-1/copy') return jsonResponse({ ok: true })
      return jsonResponse({}, 404)
    })
    render(App)
    expect(await screen.findByText('551자 · 상한 550자보다 1자 초과')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '압축 결과' })).toHaveValue(review.compressed)
    expect(screen.getByText('지수 불일치')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('combobox', { name: '시작 모델' })).toBeEnabled())
    await new Promise(resolve => setTimeout(resolve, 1100))
    expect(fetchMock.mock.calls.filter(([request]) => requestPath(request) === '/api/jobs/job-1')).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([request]) => requestPath(request).endsWith('/retry'))).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: '압축 결과 복사' }))
    await screen.findByText('압축 결과를 클립보드에 복사했습니다.')
    await fireEvent.update(screen.getByRole('combobox', { name: '시작 모델' }), 'gemini-3.7-flash')
    const regenerate = screen.getByRole('button', { name: '대본 재생성' })
    await fireEvent.click(regenerate)
    await fireEvent.click(regenerate)
    const calls = fetchMock.mock.calls.filter(([request]) => requestPath(request).endsWith('/retry'))
    expect(calls).toHaveLength(1)
    expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({ model: 'gemini-3.7-flash' })
  })

  it('시작 모델을 저장해 요청에 전달하고 작업 중에는 변경을 막는다', async () => {
    const fetchMock = installBootstrapFetch((path) => path === '/api/jobs'
      ? jsonResponse(makeJob(), 202) : jsonResponse({}, 404))
    render(App)
    await finishBootstrap()
    const select = screen.getByRole('combobox', { name: '시작 모델' })
    expect(select).toHaveValue('gemini-3.8-flash')
    expect(Array.from((select as HTMLSelectElement).options).map(option => option.value)).toEqual([
      'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite',
    ])
    await fireEvent.update(select, 'gemini-3.7-flash')
    expect(localStorage.getItem('market-compressor.model')).toBe('gemini-3.7-flash')
    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))
    await waitFor(() => expect(select).toBeDisabled())
    const call = fetchMock.mock.calls.find(([request]) => requestPath(request) === '/api/jobs')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ html: '<article>오늘의 시장</article>', model: 'gemini-3.7-flash' })
  })

  it.each([
    ['gemini-3.6-flash', 'gemini-3.6-flash'], ['removed-model', 'gemini-3.8-flash'],
  ])('저장된 모델 %s를 복원한다', async (saved, expected) => {
    localStorage.setItem('market-compressor.model', saved)
    installBootstrapFetch(() => jsonResponse({}, 404))
    render(App)
    await finishBootstrap()
    expect(screen.getByRole('combobox', { name: '시작 모델' })).toHaveValue(expected)
  })

  it('브라우저 저장소가 차단되어도 모델을 선택하고 실행한다', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    installBootstrapFetch((path) => path === '/api/jobs' ? jsonResponse(makeJob(), 202) : jsonResponse({}, 404))
    render(App)
    await finishBootstrap()
    await fireEvent.update(screen.getByRole('combobox', { name: '시작 모델' }), 'gemini-3.6-flash')
    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))
    expect(await screen.findByText('작업을 준비하는 중')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('폴백된 실제 모델을 표시하면서 시작 모델은 유지한다', async () => {
    sessionStorage.setItem('market-compressor.job-id', 'job-1')
    installBootstrapFetch(() => jsonResponse(makeJob({ state: 'completed', model: 'gemini-3.6-flash', requested_model: 'gemini-3.8-flash' })))
    render(App)
    expect(await screen.findByText('응답 모델: Gemini 3.6 Flash')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '시작 모델' })).toHaveValue('gemini-3.8-flash')
  })

  it('파일 목록 오류가 나도 클립보드 기본 입력은 불러온다', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input)
      if (path === '/api/session') return jsonResponse(session)
      if (path === '/api/files') return jsonResponse({ detail: '파일 목록을 읽지 못했습니다.' }, 500)
      if (path === '/api/clipboard/read') return jsonResponse({ text: '<div>원문</div>', format: 'text' })
      return jsonResponse({}, 404)
    }))
    render(App)
    await waitFor(() => expect(screen.getByRole('textbox', { name: '클립보드 HTML 원문' })).toHaveValue('<div>원문</div>'))
    await waitFor(() => expect(screen.getByRole('button', { name: '변환 및 1분 압축' })).toBeEnabled())
  })

  it('조회 중 작업이 사라지면 반복 조회를 멈추고 새 실행을 허용한다', async () => {
    installBootstrapFetch((path) => {
      if (path === '/api/jobs') return jsonResponse(makeJob())
      return jsonResponse({ detail: '작업 기록을 찾을 수 없습니다.' }, 404)
    })
    render(App)
    await finishBootstrap()
    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))
    await screen.findByText('작업 기록을 찾을 수 없습니다.', {}, { timeout: 3000 })
    expect(screen.getByRole('button', { name: '변환 및 1분 압축' })).toBeEnabled()
    expect(sessionStorage.getItem('market-compressor.job-id')).toBeNull()
  })

  it('서버 재시작으로 토큰이 만료되면 세션을 갱신하여 작업 기록을 복구한다', async () => {
    let sessions = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input)
      if (path === '/api/session') return jsonResponse({ ...session, token: ++sessions === 1 ? 'old' : 'new' })
      if (path === '/api/files') return jsonResponse({ files: [] })
      if (path === '/api/clipboard/read') return jsonResponse({ text: '<div>원문</div>', format: 'text' })
      if (path === '/api/jobs') return jsonResponse(makeJob())
      if (new Headers(init?.headers).get('X-App-Token') !== 'new') return jsonResponse({ detail: '세션 만료' }, 403)
      return jsonResponse(makeJob({ state: 'failed', serialized: '보관된 원문', error: '서버 종료로 중단되었습니다.' }))
    }))
    render(App)
    await finishBootstrap()
    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))
    await screen.findByText('서버 종료로 중단되었습니다.', {}, { timeout: 3000 })
    expect(screen.getByRole('button', { name: '압축 다시 시도' })).toBeEnabled()
    expect(screen.getByRole('textbox', { name: '직렬화 원문' })).toHaveValue('보관된 원문')
  })

  it('저장된 작업이 없으면 시작할 때 클립보드를 한 번 읽어 편집 가능한 원문에 넣는다', async () => {
    const fetchMock = installBootstrapFetch(() => jsonResponse({ detail: 'unexpected' }, 500))

    render(App)

    const input = await screen.findByRole('textbox', { name: '클립보드 HTML 원문' })
    await waitFor(() => expect(input).toHaveValue('<article>오늘의 시장</article>'))
    await fireEvent.update(input, '<article>수정한 시장</article>')
    expect(input).toHaveValue('<article>수정한 시장</article>')
    expect(fetchMock.mock.calls.filter(([request]) => requestPath(request) === '/api/clipboard/read')).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([request]) => requestPath(request).startsWith('/api/jobs/'))).toBe(false)

    await fireEvent.click(screen.getByRole('button', { name: '클립보드 다시 읽기' }))
    await waitFor(() => expect(input).toHaveValue('<article>오늘의 시장</article>'))
    expect(fetchMock.mock.calls.filter(([request]) => requestPath(request) === '/api/clipboard/read')).toHaveLength(2)
  })

  it('선택한 파일 경로만 작업 생성 요청에 전송한다', async () => {
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs') return jsonResponse(makeJob({ id: 'file-job', source: '오늘 시장.html' }), 202)
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    render(App)
    await finishBootstrap()

    await fireEvent.click(screen.getByRole('radio', { name: '저장된 파일' }))
    const select = screen.getByRole('combobox', { name: 'HTML 파일 선택' })
    expect(select).toHaveValue('')
    await fireEvent.click(screen.getByRole('button', { name: '파일 목록 새로고침' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([request]) => requestPath(request) === '/api/files')).toHaveLength(2))
    await screen.findByText('파일 목록을 새로 불러왔습니다.')
    await fireEvent.update(select, '오늘 시장.html')
    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([request]) => requestPath(request) === '/api/jobs')
      expect(call).toBeDefined()
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ file_path: '오늘 시장.html', model: 'gemini-3.8-flash' })
      expect(new Headers(call?.[1]?.headers).get('X-App-Token')).toBe('session-token')
    })
  })

  it('클립보드 작업을 한 번만 실행하고 완료될 때까지 폴링해 결과를 표시한다', async () => {
    const completed = makeJob({
      state: 'completed',
      serialized: '장 마감 원문',
      compressed: '첫 장면입니다.\n둘째 장면입니다.\n셋째 장면입니다.\n넷째 장면입니다.\n마지막 장면입니다.',
      scene_count: 6,
      body_char_count: 472,
      elapsed_seconds: 4.2,
      attempt: 1,
      key_number: 1,
      events: [
        { at: '2026-09-11T00:00:00Z', message: '원문 직렬화 완료' },
        { at: '2026-09-11T00:00:04Z', message: '압축 완료' },
      ],
    })
    let polls = 0
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs') return jsonResponse(makeJob(), 202)
      if (path === '/api/jobs/job-1') {
        polls += 1
        return jsonResponse(completed)
      }
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    render(App)
    await finishBootstrap()

    const runButton = screen.getByRole('button', { name: '변환 및 1분 압축' })
    await fireEvent.click(runButton)
    await fireEvent.click(runButton)
    expect(fetchMock.mock.calls.filter(([request]) => requestPath(request) === '/api/jobs')).toHaveLength(1)

    await waitFor(() => expect(polls).toBe(1), { timeout: 2000 })
    expect(await screen.findByRole('textbox', { name: '압축 결과' })).toHaveValue(completed.compressed)
    expect(screen.getByText('입력 6개 → 최종 5줄 대본')).toBeInTheDocument()
    expect(sessionStorage.getItem('market-compressor.job-id')).toBe('job-1')
  })

  it('409 응답의 작업 ID를 보존하고 이미 진행 중인 작업을 복원한다', async () => {
    const active = makeJob({ id: 'server-active', state: 'requesting', attempt: 1, key_number: 3 })
    installBootstrapFetch((path) => {
      if (path === '/api/jobs') return jsonResponse({ detail: '이미 실행 중인 작업이 있습니다.', job_id: 'server-active' }, 409)
      if (path === '/api/jobs/server-active') return jsonResponse(active)
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    render(App)
    await finishBootstrap()

    await fireEvent.click(screen.getByRole('button', { name: '변환 및 1분 압축' }))

    expect(await screen.findByText('Gemini 응답을 기다리는 중')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('이미 진행 중인 작업을 이어서 표시합니다.')
    expect(sessionStorage.getItem('market-compressor.job-id')).toBe('server-active')
  })

  it('실패한 작업의 재시도는 원본을 다시 보내지 않고 서버 스냅샷 경로를 사용한다', async () => {
    sessionStorage.setItem('market-compressor.job-id', 'failed-job')
    const failed = makeJob({ id: 'failed-job', state: 'failed', error: '형식 검증에 실패했습니다.', serialized: '보존된 직렬화 원문' })
    const retried = makeJob({ id: 'retry-job', parent_id: 'failed-job' })
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs/failed-job') return jsonResponse(failed)
      if (path === '/api/jobs/failed-job/retry') return jsonResponse(retried, 202)
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    render(App)

    await waitFor(() => expect(screen.getByRole('combobox', { name: '시작 모델' })).toBeEnabled())
    await fireEvent.update(screen.getByRole('combobox', { name: '시작 모델' }), 'gemini-3.6-flash')
    await fireEvent.click(await screen.findByRole('button', { name: '압축 다시 시도' }))

    await waitFor(() => {
      const retryCall = fetchMock.mock.calls.find(([request]) => requestPath(request) === '/api/jobs/failed-job/retry')
      expect(retryCall).toBeDefined()
      expect(JSON.parse(String(retryCall?.[1]?.body))).toEqual({ model: 'gemini-3.6-flash' })
      expect(sessionStorage.getItem('market-compressor.job-id')).toBe('retry-job')
    })
    expect(fetchMock.mock.calls.some(([request]) => requestPath(request) === '/api/clipboard/read')).toBe(false)
  })

  it('직렬화 스냅샷이 없는 실패 작업에는 재시도 버튼을 표시하지 않는다', async () => {
    sessionStorage.setItem('market-compressor.job-id', 'failed-job')
    installBootstrapFetch((path) => {
      if (path === '/api/jobs/failed-job') return jsonResponse(makeJob({ id: 'failed-job', state: 'failed', error: '원문 변환 실패' }))
      return jsonResponse({ detail: 'unexpected' }, 500)
    })

    render(App)

    expect(await screen.findByText('원문 변환 실패')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '압축 다시 시도' })).not.toBeInTheDocument()
  })

  it.each(['completed', 'failed'] as const)('%s 결과를 같은 article에서 복사하고 다운로드한다', async (state) => {
    sessionStorage.setItem('market-compressor.job-id', 'job-1')
    const completed = makeJob({ state, error: state === 'failed' ? '응답 검증 실패: 분량 미달' : null, serialized: '직렬화', compressed: '최종 압축본', scene_count: 5, body_char_count: 480 })
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs/job-1') return jsonResponse(completed)
      if (path === '/api/jobs/job-1/copy') return jsonResponse({ ok: true })
      if (path === '/api/jobs/job-1/artifacts/compressed.txt') return new Response('최종 압축본', { headers: { 'Content-Type': 'text/plain; charset=utf-8' } })
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    const createObjectURL = vi.fn(() => 'blob:download')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(App)

    await fireEvent.click(await screen.findByRole('button', { name: '압축 결과 복사' }))
    expect(screen.getByRole('textbox', { name: '압축 결과' }).closest('article')).not.toBeNull()
    if (state === 'failed') {
      expect(screen.getByRole('alert')).toHaveTextContent('응답 검증 실패: 분량 미달')
      expect(screen.getByRole('alert').closest('.progress-column')).not.toBeNull()
      expect(screen.getByText('검증 실패 대본')).toBeInTheDocument()
      expect(screen.queryByText('최종 5줄 영상 대본')).not.toBeInTheDocument()
    } else {
      expect(screen.getByRole('status')).toHaveTextContent('영상 원고가 완성되었습니다')
    }
    await screen.findByText('압축 결과를 클립보드에 복사했습니다.')
    await fireEvent.click(screen.getByRole('button', { name: '압축 결과 다운로드' }))

    await waitFor(() => expect(clickSpy).toHaveBeenCalledOnce())
    const copyCall = fetchMock.mock.calls.find(([request]) => requestPath(request).endsWith('/copy'))
    expect(JSON.parse(String(copyCall?.[1]?.body))).toEqual({ stage: 'compressed' })
    const downloadCall = fetchMock.mock.calls.find(([request]) => requestPath(request).includes('/artifacts/compressed.txt'))
    expect(new Headers(downloadCall?.[1]?.headers).get('X-App-Token')).toBe('session-token')
    expect(createObjectURL).toHaveBeenCalledOnce()
  })

  it('새로고침 시 진행 중 작업을 복원하고 시작 클립보드를 덮어쓰지 않는다', async () => {
    sessionStorage.setItem('market-compressor.job-id', 'active-job')
    const active = makeJob({ id: 'active-job', state: 'requesting', serialized: '복원한 직렬화', attempt: 2, key_number: 3 })
    const fetchMock = installBootstrapFetch((path) => {
      if (path === '/api/jobs/active-job') return jsonResponse(active)
      return jsonResponse({ detail: 'unexpected' }, 500)
    })

    render(App)

    expect(await screen.findByText('Gemini 응답을 기다리는 중')).toBeInTheDocument()
    expect(screen.getByText('시도 2 / 3')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '직렬화 원문' })).toHaveValue('복원한 직렬화')
    expect(fetchMock.mock.calls.some(([request]) => requestPath(request) === '/api/clipboard/read')).toBe(false)
  })
})
