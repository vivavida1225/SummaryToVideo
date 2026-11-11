import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/vue'
import { afterEach, expect, it, vi } from 'vitest'
import InfoXDownloads from './InfoXDownloads.vue'
import { api } from '../api'
import type { DownloadRecord } from '../types'
import { flushPromises } from '@vue/test-utils'

vi.mock('../api',()=>({api:{infoxDownloads:vi.fn(),startInfoxDownload:vi.fn(),retryInfoxDownload:vi.fn()}}))
afterEach(()=>{cleanup();vi.restoreAllMocks();sessionStorage.clear()})
const partial={id:'download-1',publication_id:'pub-1',kind:'all',automatic:false,state:'partial',error_code:null,
  items:[{kind:'script',filename:'261104_대본.txt',state:'completed',path:'C:\\Downloads\\261104_대본.txt',size:10,sha256:'hash',error_code:null},
    {kind:'video',filename:'261104_영상시황.mp4',state:'failed',path:null,size:null,sha256:null,error_code:'timeout'}]} as DownloadRecord

it('loads_results_without_writing_and_allows_clipboard_failed_upload_download',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[]})
  vi.mocked(api.startInfoxDownload).mockResolvedValue(partial)
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  const button=await screen.findByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})
  await waitFor(()=>expect(button).toBeEnabled())
  expect(api.startInfoxDownload).not.toHaveBeenCalled()
  await fireEvent.click(button)
  expect(api.startInfoxDownload).toHaveBeenCalledWith('pub-1',expect.objectContaining({kind:'all'}))
  expect(await screen.findByText('일부 다운로드 완료')).toBeInTheDocument()
  expect(screen.getByText('C:\\Downloads\\261104_대본.txt')).toBeInTheDocument()
})

it('shows_partial_results_and_retries_only_through_download_api',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[partial]})
  vi.mocked(api.retryInfoxDownload).mockResolvedValue({...partial,state:'completed'})
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  await fireEvent.click(await screen.findByRole('button',{name:'실패 파일 다시 다운로드'}))
  expect(api.retryInfoxDownload).toHaveBeenCalledWith('download-1',expect.any(String))
  expect(await screen.findByText('다운로드 완료')).toBeInTheDocument()
})

it('prevents_all_download_before_verified_upload_and_during_an_active_download',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[{...partial,state:'running'}]})
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:false}})
  expect(screen.queryByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})).not.toBeInTheDocument()
  expect(await screen.findByText('다운로드 중')).toBeInTheDocument()
  expect(api.startInfoxDownload).not.toHaveBeenCalled()
})

it('shows automatic three-file results and retry before upload without offering manual all',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[{...partial,automatic:true}]})
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:false}})
  expect(await screen.findByText('렌더 완료 대본·원본·영상 자동 다운로드')).toBeVisible()
  expect(screen.queryByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})).not.toBeInTheDocument()
  expect(screen.getByRole('button',{name:'실패 파일 다시 다운로드'})).toBeEnabled()
})

it('waits_for_a_queued_automatic_operation_without_offering_retry_or_new_work',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[{...partial,state:'queued',automatic:true}]})
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  expect(await screen.findByText('진행 중인 파일 다운로드 완료 대기')).toBeVisible()
  expect(screen.getByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})).toBeDisabled()
  expect(screen.queryByRole('button',{name:'실패 파일 다시 다운로드'})).not.toBeInTheDocument()
  expect(api.startInfoxDownload).not.toHaveBeenCalled()
})

it('ignores_a_stale_poll_after_a_new_download_response',async()=>{
  let release!: (result: {downloads: DownloadRecord[]})=>void
  vi.mocked(api.infoxDownloads).mockResolvedValueOnce({downloads:[]})
    .mockImplementationOnce(()=>new Promise(resolve=>{release=resolve}))
  vi.mocked(api.startInfoxDownload).mockResolvedValue(partial)
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  await waitFor(()=>expect(api.infoxDownloads).toHaveBeenCalledTimes(2),{timeout:3500})
  await fireEvent.click(screen.getByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'}))
  expect(await screen.findByText('일부 다운로드 완료')).toBeInTheDocument()
  release({downloads:[]});await flushPromises()
  expect(screen.getByText('일부 다운로드 완료')).toBeInTheDocument()
})

it('keeps_the_same_intent_after_lost_response_and_reload',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[]})
  vi.mocked(api.startInfoxDownload).mockRejectedValueOnce(new Error('lost')).mockResolvedValue(partial)
  const view=render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  const button=await screen.findByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})
  await waitFor(()=>expect(button).toBeEnabled());await fireEvent.click(button)
  await screen.findByText(/다운로드 접수 결과를 확인하지 못했습니다/)
  const first=vi.mocked(api.startInfoxDownload).mock.calls.at(-1)![1].intent_id
  view.unmount()
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  const retry=await screen.findByRole('button',{name:'다운로드 접수 다시 확인'})
  await waitFor(()=>expect(retry).toBeEnabled());await fireEvent.click(retry)
  expect(vi.mocked(api.startInfoxDownload).mock.calls.at(-1)![1].intent_id).toBe(first)
})

it('explains_when_all_download_coalesces_with_existing_video_work',async()=>{
  vi.mocked(api.infoxDownloads).mockResolvedValue({downloads:[]})
  vi.mocked(api.startInfoxDownload).mockResolvedValue({...partial,kind:'video',automatic:true,state:'running',items:[partial.items[1]!]})
  render(InfoXDownloads,{props:{publicationId:'pub-1',uploadedVerified:true}})
  const button=await screen.findByRole('button',{name:'대본·원본·영상 3개 파일 다운로드'})
  await waitFor(()=>expect(button).toBeEnabled());await fireEvent.click(button)
  expect(await screen.findByText(/기존 영상 다운로드가 진행 중입니다/)).toBeInTheDocument()
  expect(button).toBeDisabled()
})
