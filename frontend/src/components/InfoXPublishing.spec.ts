import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/vue'
import InfoXPublishing from './InfoXPublishing.vue'
import { api } from '../api'
import { PublishingControl } from '../infoxPublishing'
import type { InfoXRun } from '../types'

vi.mock('../api',()=>({api:{
  latestInfoxPublication:vi.fn(), startInfoxPublication:vi.fn(), infoxPublication:vi.fn(),
  copyInfoxPublicationLink:vi.fn(), infoxPublicationPreview:vi.fn(),
  infoxPublicationHistory:vi.fn().mockResolvedValue({publications:[]}),rerenderInfoxPublication:vi.fn(),
}}))
const run = {id:'run-1',video_id:'video-1',stage:'ready_for_editor',requires_reconciliation:false,
  asset_save:{state:'saved_verified'}} as InfoXRun
const result = {id:'pub-1',run_id:'run-1',video_id:'video-1',stage:'rendering',progress:null,attempt_number:1,
  snapshot:{market_date:'2026-11-04',title:'261104 AI 영상 시황',description:'2026년 11월 4일 AI 영상 시황',privacy_status:'unlisted'},
  uploaded_verified:false,clipboard_state:'not_started',share_url:null}
let control: PublishingControl
afterEach(()=>{control?.dispose(); cleanup(); vi.restoreAllMocks()})
beforeEach(()=>{vi.mocked(api.infoxPublicationHistory).mockResolvedValue({publications:[]})})

function mount(target: InfoXRun = run, eligible = true) {
  control = new PublishingControl(api, '2026-11-04')
  void control.restore(target.id, eligible)
  return render(InfoXPublishing, { props: { run: target, control } })
}

it('locks_the_recorded_date_while_a_publication_runs', async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:result} as never)
  mount()
  await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('렌더 진행 중'))
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.getByLabelText('게시 기준일')).toBeDisabled()
  expect(screen.queryByRole('button',{name:'편집 완료 · 렌더 및 유튜브 업로드'})).not.toBeInTheDocument()
})

it('clipboard_failure_keeps_the_upload_record_without_restarting', async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:{...result,stage:'clipboard_failed',uploaded_verified:true,share_url:'https://youtu.be/abcdefghijk'}} as never)
  vi.mocked(api.copyInfoxPublicationLink).mockResolvedValue({...result,stage:'completed',uploaded_verified:true,clipboard_state:'copied',share_url:'https://youtu.be/abcdefghijk'} as never)
  mount()
  await waitFor(()=>expect(screen.getByText('업로드 완료 · 링크 복사 실패')).toBeInTheDocument())
  await control.copy()
  await waitFor(()=>expect(screen.getByText('업로드 완료 · 링크 복사 완료')).toBeInTheDocument())
  expect(api.startInfoxPublication).not.toHaveBeenCalled()
})

it('previews_fixed_metadata_and_charge_before_start', async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:null})
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:true,credits:3,duration_seconds:61,can_start:true,blocked_reason:null,latest_publication_id:null,has_remote_render:false})
  mount()
  await waitFor(()=>expect(control.canStart.value).toBe(true))
  expect(screen.getByText('261104 AI 영상 시황')).toBeInTheDocument()
  expect(screen.getByText(/과금 대상 · 예상 차감 크레딧 3 · 영상 길이 61초/)).toBeInTheDocument()
  expect(screen.getByText('일부공개 (unlisted)')).toBeInTheDocument()
})

it('keeps_an_edited_date_when_the_same_run_becomes_editor_ready', async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:null})
  vi.mocked(api.infoxPublicationPreview).mockClear()
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:true,can_start:true,blocked_reason:null,latest_publication_id:null,has_remote_render:false})
  const pending = {...run,stage:'generating',asset_save:{state:'not_started'}} as InfoXRun
  const {rerender}=mount(pending, false)
  await waitFor(()=>expect(api.latestInfoxPublication).toHaveBeenCalled())
  expect(api.infoxPublicationPreview).not.toHaveBeenCalled()
  await waitFor(()=>expect(screen.getByLabelText('게시 기준일')).toBeEnabled())
  await fireEvent.update(screen.getByLabelText('게시 기준일'),'2026-11-03')
  await rerender({run, control})
  await control.setEligibility(true)
  await waitFor(()=>expect(control.canStart.value).toBe(true))
  expect(screen.getByLabelText('게시 기준일')).toHaveValue('2026-11-03')
})

it('shows_history_and_cancels_a_prepared_rerender_without_sending_it', async () => {
  const old={...result,stage:'completed',attempt_number:1,uploaded_verified:true,share_url:'https://youtu.be/abcdefghijk'}
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:old} as never)
  vi.mocked(api.infoxPublicationHistory).mockResolvedValue({publications:[old]} as never)
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:false,can_start:true,blocked_reason:null,latest_publication_id:old.id,has_remote_render:true})
  mount()
  await waitFor(()=>expect(control.canPrepare.value).toBe(true))
  await control.prepareNew()
  await waitFor(()=>expect(screen.getByLabelText('게시 기준일')).toBeEnabled())
  expect(screen.getByLabelText('게시 기준일')).toHaveValue('2026-11-04')
  expect(api.rerenderInfoxPublication).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button',{name:'준비 취소'}))
  expect(screen.getByLabelText('게시 기준일')).toBeDisabled()
})
