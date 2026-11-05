import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup } from '@testing-library/vue'
import InfoXPublishing from './InfoXPublishing.vue'
import { api } from '../api'
import type { InfoXRun } from '../types'

vi.mock('../api',()=>({api:{
  latestInfoxPublication:vi.fn(), startInfoxPublication:vi.fn(), infoxPublication:vi.fn(),
  copyInfoxPublicationLink:vi.fn(), infoxPublicationPreview:vi.fn(),
  infoxPublicationHistory:vi.fn().mockResolvedValue({publications:[]}),rerenderInfoxPublication:vi.fn(),
}}))
const run = {id:'run-1',video_id:'video-1',stage:'ready_for_editor',requires_reconciliation:false,
  asset_save:{state:'saved_verified'}} as InfoXRun
const result = {id:'pub-1',run_id:'run-1',video_id:'video-1',stage:'rendering',progress:null,
  snapshot:{market_date:'2026-11-04',title:'261104 AI 영상 시황',description:'2026년 11월 4일 AI 영상 시황',privacy_status:'unlisted'},
  uploaded_verified:false,clipboard_state:'not_started',share_url:null}
afterEach(()=>{cleanup(); vi.restoreAllMocks()})
beforeEach(()=>{vi.mocked(api.infoxPublicationHistory).mockResolvedValue({publications:[]})})

it('null_progress_is_not_fake_percent',async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:result} as never)
  render(InfoXPublishing,{props:{run,initialMarketDate:'2026-11-04'}})
  await waitFor(()=>expect(screen.getByText(/진행률 확인 중/)).toBeInTheDocument())
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.getByLabelText('게시 기준일')).toBeDisabled()
  expect(screen.getByRole('button',{name:'편집 완료 · 렌더 및 유튜브 업로드'})).toBeDisabled()
})
it('clipboard_failure_shows_uploaded_link_and_only_recopies',async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:{...result,stage:'clipboard_failed',uploaded_verified:true,share_url:'https://youtu.be/abcdefghijk'}} as never)
  vi.mocked(api.copyInfoxPublicationLink).mockResolvedValue({...result,stage:'completed',uploaded_verified:true,clipboard_state:'copied',share_url:'https://youtu.be/abcdefghijk'} as never)
  render(InfoXPublishing,{props:{run,initialMarketDate:'2026-11-04'}})
  await waitFor(()=>expect(screen.getByText('업로드 완료 · 링크 복사 실패')).toBeInTheDocument())
  expect(screen.getByRole('link',{name:'https://youtu.be/abcdefghijk'})).toHaveAttribute('href','https://youtu.be/abcdefghijk')
  await fireEvent.click(screen.getByRole('button',{name:'링크 다시 복사'}))
  await waitFor(()=>expect(screen.getByText('업로드 완료 · 링크 복사 완료')).toBeInTheDocument())
  expect(api.startInfoxPublication).not.toHaveBeenCalled()
})
it('previews_fixed_metadata_and_starts_on_one_click',async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:null})
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:true,can_start:true,blocked_reason:null,latest_publication_id:null,has_remote_render:false})
  vi.mocked(api.startInfoxPublication).mockResolvedValue({...result,progress:42} as never)
  render(InfoXPublishing,{props:{run,initialMarketDate:'2026-11-04'}})
  const button=screen.getByRole('button',{name:'편집 완료 · 렌더 및 유튜브 업로드'})
  await waitFor(()=>expect(button).toBeEnabled())
  expect(screen.getByText('261104 AI 영상 시황')).toBeInTheDocument()
  await fireEvent.click(button)
  await waitFor(()=>expect(screen.getByRole('progressbar')).toHaveAttribute('value','42'))
})

it('enables_first_publish_when_the_same_run_becomes_editor_ready',async () => {
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:null})
  vi.mocked(api.infoxPublicationPreview).mockClear()
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:true,can_start:true,blocked_reason:null,latest_publication_id:null,has_remote_render:false})
  const {rerender}=render(InfoXPublishing,{props:{run:{...run,stage:'generating',asset_save:{state:'not_started'}} as InfoXRun}})
  await waitFor(()=>expect(api.latestInfoxPublication).toHaveBeenCalled())
  expect(api.infoxPublicationPreview).not.toHaveBeenCalled()
  await waitFor(()=>expect(screen.getByLabelText('게시 기준일')).toBeEnabled())
  await fireEvent.update(screen.getByLabelText('게시 기준일'),'2026-11-03')
  await rerender({run})
  await waitFor(()=>expect(screen.getByRole('button',{name:'편집 완료 · 렌더 및 유튜브 업로드'})).toBeEnabled())
  expect(screen.getByLabelText('게시 기준일')).toHaveValue('2026-11-03')
})

it('shows_history_and_prepares_a_rerender_without_sending_it',async()=>{
  const old={...result,stage:'completed',attempt_number:1,uploaded_verified:true,share_url:'https://youtu.be/abcdefghijk'}
  vi.mocked(api.latestInfoxPublication).mockResolvedValue({publication:old} as never)
  vi.mocked(api.infoxPublicationHistory).mockResolvedValue({publications:[old]} as never)
  vi.mocked(api.infoxPublicationPreview).mockResolvedValue({available:true,charged:false,can_start:true,blocked_reason:null,latest_publication_id:old.id,has_remote_render:true})
  render(InfoXPublishing,{props:{run}})
  await fireEvent.click(await screen.findByRole('button',{name:'새 회차 렌더·업로드 준비'}))
  await waitFor(()=>expect(screen.getByLabelText('게시 기준일')).toBeEnabled())
  expect(screen.getByLabelText('게시 기준일')).toHaveValue('2026-11-04')
  expect(api.rerenderInfoxPublication).not.toHaveBeenCalled()
  await fireEvent.click(screen.getByRole('button',{name:'준비 취소'}))
  expect(screen.getByLabelText('게시 기준일')).toBeDisabled()
})
