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
  tracking_active?: boolean
  continuation?: 'manual' | 'automatic'
  review_mode?: 'automatic' | 'manual_review'
  manual_review_started_at?: string | null
  can_enter_manual_review?: boolean
  can_open_editor?: boolean
  can_prepare_publication?: boolean
  review_blocked_reason?: string | null
  asset_audit?: InfoXAssetAudit
  asset_counts?: Record<string, number>
  replacement_plan?: InfoXReplacementPlan
  asset_save?: InfoXAssetSave
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
  editor_url?: string | null
  tracking?: {
    completion_seen: boolean
    task_status: string
    progress: number | null
    error_code?: string | null
    reconnects?: number
    draft?: { identity_matches: boolean; scenes_match: boolean; voices_ready: boolean; captions_ready: boolean }
  }
}

export interface InfoXAssetSave {
  state: string
  receipt_state: string
  error_code?: string | null
  write_error_code?: string | null
  write_status?: number | null
  read_error_code?: string | null
  response_version?: string | null
  verified_version?: string | null
}

export interface EditorAttempt {
  id: string
  run_id: string
  mode: 'automatic' | 'manual'
  version: number
  state: string
  video_id: string | null
  editor_url: string | null
  editor_status: string
}

export interface PublicationRecord {
  id: string
  run_id: string
  video_id: string
  attempt_number: number
  previous_publication_id: string | null
  created_at: string
  version: number
  stage: string
  failed_stage: string | null
  error_code: string | null
  progress: number | null
  snapshot: { market_date: string; title: string; description: string; privacy_status: 'unlisted'
    review_confirmed_at?: string | null; reviewed_remote_updated_at?: string | null; review_warning_codes?: string[] }
  uploaded_verified: boolean
  youtube_video_id: string | null
  share_url: string | null
  clipboard_state: string
}
export interface DownloadItem {
  kind: 'script' | 'source' | 'video'
  filename: string
  state: 'pending' | 'downloading' | 'completed' | 'failed'
  path: string | null
  size: number | null
  sha256: string | null
  error_code: string | null
}
export interface DownloadRecord {
  id: string
  publication_id: string
  kind: 'video' | 'all'
  automatic: boolean
  state: 'queued' | 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'needs_attention'
  items: DownloadItem[]
  error_code: string | null
}
export interface PublishInput { intent_id: string; market_date: string; review_confirmed?: boolean; reviewed_remote_updated_at?: string }
export interface RerenderInput extends PublishInput { previous_publication_id: string }
export interface PublishCharge {
  remote_updated_at?: string | null
  available: boolean; charged: boolean | null; credits?: number; duration_seconds?: number
  can_start: boolean; blocked_reason: string | null; latest_publication_id: string | null; has_remote_render: boolean
}

export interface InfoXReplacementPlan {
  state: string
  error_code?: string | null
  policy?: { search: string; reuse_policy: string }
  search_evidence?: { total: number; pages: number; search?: string } | null
  checks?: Record<string, boolean>
  selections: Array<{
    target: { scene_index: number; layer_id: string; item_index: number | null }
    slot_frames: number
    candidate: { id: string; title: string; video_id: string; bucket: string; segment: string;
      frame_index: number; timestamp: number; duration: number }
    available_candidates?: number | null
    clips?: Array<{
      candidate: { id: string; title: string; video_id: string; bucket: string; segment: string;
        frame_index: number; timestamp: number; duration: number }
      slot_frames: number
      result_item_index: number | null
    }>
  }>
}

export interface InfoXAssetAudit {
  state: string
  audited_at?: string | null
  error_code?: string | null
  issues: Array<{ scene_index?: number | null; layer_id?: string | null; code: string }>
  items: Array<{
    scene_index: number
    scene_id?: string | null
    layer_id: string
    item_index: number | null
    verdict: string
    reason: string
    reference: { kind: string; bucket?: string | null; filename?: string | null; segment?: string | null }
    hits: Array<{ term: string; field: string; source: string }>
    metadata_sources: string[]
  }>
}
