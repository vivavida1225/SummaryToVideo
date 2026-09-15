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
  attempted_models?: string[]
  key_number: number | null
  retry_at: string | null
  serialized: string | null
  compressed: string | null
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
}

export interface ModelOption {
  id: string
  label: string
}

export interface SourceFile {
  path: string
  size: number
}
