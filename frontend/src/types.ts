export type JobState =
  | 'queued'
  | 'serializing'
  | 'copying_serialized'
  | 'requesting'
  | 'retry_wait'
  | 'validating'
  | 'copying_compressed'
  | 'completed'
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
  max_attempts: 3
  key_number: number | null
  retry_at: string | null
  serialized: string | null
  compressed: string | null
  scene_count: number | null
  body_char_count: number | null
  warnings: string[]
  error: string | null
  output_dir: string
  events: JobEvent[]
}

export interface SessionInfo {
  token: string
  model: string
  configured_keys: number[]
}

export interface SourceFile {
  path: string
  size: number
}
