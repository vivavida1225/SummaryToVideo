import type { Job, MyassetSource, SessionInfo, SourceFile } from './types'

interface ApiErrorBody {
  detail?: string
  job_id?: string
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly jobId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

let appToken = ''

async function parseError(response: Response): Promise<never> {
  let body: ApiErrorBody = {}
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    // A useful fallback is provided below for non-JSON server failures.
  }
  throw new ApiError(body.detail?.trim() || `요청을 처리하지 못했습니다. (${response.status})`, response.status, body.job_id)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-App-Token', appToken)
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) return parseError(response)
  return response.json() as Promise<T>
}

export async function bootstrapSession(): Promise<SessionInfo> {
  const response = await fetch('/api/session')
  if (!response.ok) return parseError(response)
  const session = (await response.json()) as SessionInfo
  appToken = session.token
  return session
}

export const api = {
  myassetSource(baseDate: string, gubun: number): Promise<MyassetSource> {
    return request('/api/sources/myasset', { method: 'POST', body: JSON.stringify({ base_date: baseDate, gubun }) })
  },

  async files(): Promise<SourceFile[]> {
    const response = await request<{ files: SourceFile[] }>('/api/files')
    return response.files
  },

  clipboard(): Promise<{ text: string; format: 'text' | 'html' }> {
    return request('/api/clipboard/read', { method: 'POST' })
  },

  createJob(source: ({ html: string } | { file_path: string }) & { model?: string }): Promise<Job> {
    return request('/api/jobs', { method: 'POST', body: JSON.stringify(source) })
  },

  job(id: string): Promise<Job> {
    return request(`/api/jobs/${encodeURIComponent(id)}`)
  },

  saveCompressed(id: string, text: string, revision: number): Promise<Job> {
    return request(`/api/jobs/${encodeURIComponent(id)}/compressed`, {
      method: 'POST', body: JSON.stringify({ text, revision }),
    })
  },

  retry(id: string, model?: string): Promise<Job> {
    return request(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST', body: JSON.stringify({ model }) })
  },

  copy(id: string, stage: 'serialized' | 'compressed'): Promise<{ ok: true }> {
    return request(`/api/jobs/${encodeURIComponent(id)}/copy`, {
      method: 'POST',
      body: JSON.stringify({ stage }),
    })
  },

  async artifact(id: string, name: 'serialized.txt' | 'compressed.txt'): Promise<Blob> {
    const response = await fetch(`/api/jobs/${encodeURIComponent(id)}/artifacts/${name}`, {
      headers: { 'X-App-Token': appToken },
    })
    if (!response.ok) return parseError(response)
    return response.blob()
  },
}
