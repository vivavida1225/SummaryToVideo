export type JobState =
  | 'queued'
  | 'serializing'
  | 'copying_serialized'
  | 'requesting'
  | 'retry_wait'
  | 'validating'
  | 'copying_compressed'
  | 'completed'
  | 'needs_review'
  | 'failed'

export interface JobEvent {
  at: string
  message: string
}

export interface Job {
  id: string
  parent_id: string | null
  state: JobState
  source: string
  created_at: string
  elapsed_seconds: number
  attempt: number
  max_attempts: number
  model?: string
  requested_model?: string | null
  transcribe_numbers?: boolean
  attempted_models?: string[]
  key_number: number | null
  retry_at: string | null
  serialized: string | null
  compressed: string | null
  revision?: number
  edited_at?: string | null
  scene_count: number | null
  body_char_count: number | null
  excess_char_count?: number
  validation_issues?: string[]
  warnings: string[]
  error: string | null
  output_dir: string
  events: JobEvent[]
}

export interface SessionInfo {
  token: string
  model: string
  models: ModelOption[]
  configured_keys: number[]
  configured_keys_by_provider: Record<'gemini' | 'openai', number[]>
}

export interface ModelOption {
  provider: 'gemini' | 'openai'
  id: string
  label: string
}

export interface SourceFile {
  path: string
  size: number
}

export interface MyassetSource {
  html: string
  source_url: string
  base_date: string
  gubun: number
}

export interface InfoXPrepareInput {
  intent_id: string
  revision: number
  settings: { title: string; auto_replace: boolean }
  review_confirmed: boolean
  new_run: boolean
}

export interface InfoXRun {
  id: string
  stage: string
  version: number
  requires_reconciliation: boolean
  snapshot: {
    job_id: string
    revision: number
    text: string
    text_sha256: string
    scenes: Array<{ line_number: number; scene_index: number; text: string }>
    settings: { title: string; template_id: number; voice_id: string; voice_speed: string; auto_replace: boolean }
  }
  video_id: string | null
  task_id: string | null
}
