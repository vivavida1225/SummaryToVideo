import { afterEach, expect, it, vi } from 'vitest'
import { PublishingControl, formatPublishMetadata, seoulToday } from './infoxPublishing'
import type { PublicationRecord } from './types'

export function publication(overrides: Partial<PublicationRecord> = {}): PublicationRecord {
  return { id:'pub-1', run_id:'run-1', video_id:'video-1', attempt_number:1,previous_publication_id:null,created_at:'2026-11-04T00:00:00Z',version:0, stage:'rendering', failed_stage:null,
    error_code:null, progress:42, snapshot:{market_date:'2026-11-04', title:'261104 AI 영상 시황',
    description:'2026년 11월 4일 AI 영상 시황', privacy_status:'unlisted'}, uploaded_verified:false,
    share_url:null, youtube_video_id:null, clipboard_state:'not_started', ...overrides }
}
function setup() {
  const port = {
    latestInfoxPublication:vi.fn().mockResolvedValue({publication:null}),
    infoxPublicationHistory:vi.fn().mockResolvedValue({publications:[]}),
    rerenderInfoxPublication:vi.fn().mockResolvedValue(publication({id:'pub-2',attempt_number:2,previous_publication_id:'pub-1'})),
    startInfoxPublication:vi.fn().mockResolvedValue(publication()),
    infoxPublication:vi.fn().mockResolvedValue(publication({stage:'completed'})),
    copyInfoxPublicationLink:vi.fn().mockResolvedValue(publication({stage:'completed'})),
    infoxPublicationPreview:vi.fn().mockResolvedValue({available:true,charged:true,can_start:true,blocked_reason:null,latest_publication_id:null,has_remote_render:false,remote_updated_at:'2026-11-04T00:00:00Z'}),
  }
  return {port, control:new PublishingControl(port,'2026-11-04')}
}
afterEach(() => vi.useRealTimers())

it('requires fresh manual confirmation for every attempt and binds the saved version', async () => {
  const { port, control } = setup()
  port.infoxPublicationPreview.mockResolvedValue({available:true, charged:true, can_start:true, blocked_reason:null,
    latest_publication_id:null, has_remote_render:false, remote_updated_at:'2026-11-12T06:43:40Z'} as never)
  await control.restore('run-1')
  await control.start()
  expect(port.startInfoxPublication).not.toHaveBeenCalled()
  await control.confirmReview(true)
  expect(control.canStart.value).toBe(true)
  await control.start()
  expect(port.startInfoxPublication.mock.calls[0]![1]).toMatchObject({review_confirmed:true,
    reviewed_remote_updated_at:'2026-11-12T06:43:40Z'})
  await control.restore('run-1')
  expect(control.reviewConfirmed.value).toBe(false)
  control.dispose()
})

it('invalid_calendar_dates_do_not_crash_the_preview',()=>{
  expect(formatPublishMetadata('2026-99-99')).toBeNull()
  expect(formatPublishMetadata('2026-02-30')).toBeNull()
})

it('starts_once_and_shows_stage_progress',async () => {
  const {port,control}=setup()
  await control.restore('run-1')
  await control.confirmReview(true)
  await Promise.all([control.start(),control.start()])
  expect(port.startInfoxPublication).toHaveBeenCalledTimes(1)
  expect(port.startInfoxPublication.mock.calls[0]![1].market_date).toBe('2026-11-04')
  expect(control.record.value?.progress).toBe(42)
  control.dispose()
})
it('restores_by_get_without_post',async () => {
  const {port,control}=setup()
  port.latestInfoxPublication.mockResolvedValue({publication:publication({stage:'completed'})} as never)
  await control.restore('run-1')
  expect(port.startInfoxPublication).not.toHaveBeenCalled()
  expect(port.copyInfoxPublicationLink).not.toHaveBeenCalled()
  expect(control.record.value?.stage).toBe('completed')
  control.dispose()
})
it('stale_response_cannot_replace_other_run',async () => {
  const {port,control}=setup()
  let resolve!:(v:unknown)=>void
  port.latestInfoxPublication.mockReturnValueOnce(new Promise(r=>{resolve=r}) as never)
  const old=control.restore('run-1')
  await control.restore('run-2')
  resolve({publication:publication()}); await old
  expect(control.record.value).toBeNull()
  await control.confirmReview(true); await control.start()
  expect(port.startInfoxPublication.mock.calls[0]![0]).toBe('run-2')
  control.dispose()
})
it('date_and_metadata_stay_frozen_across_midnight',async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T14:59:59Z'))
  const {control}=setup()
  expect(seoulToday()).toBe('2026-11-04')
  await control.restore('run-1'); await control.confirmReview(true); await control.start()
  vi.setSystemTime(new Date('2026-11-04T15:00:01Z'))
  expect(control.marketDate.value).toBe('2026-11-04')
  expect(control.record.value?.snapshot.title).toBe('261104 AI 영상 시황')
  expect(formatPublishMetadata('2026-11-04')).toEqual({title:'261104 AI 영상 시황',description:'2026년 11월 4일 AI 영상 시황'})
  control.dispose()
})
it('lost_local_post_response_is_recovered_by_get_without_second_post',async () => {
  const {port,control}=setup()
  await control.restore('run-1')
  port.startInfoxPublication.mockRejectedValue(new Error('lost'))
  port.latestInfoxPublication.mockResolvedValue({publication:publication({stage:'needs_attention'})} as never)
  await control.confirmReview(true); await control.start(); await control.confirmReview(true); await control.start()
  expect(port.startInfoxPublication).toHaveBeenCalledTimes(1)
  expect(control.record.value?.stage).toBe('needs_attention')
  control.dispose()
})

it('prepares_a_new_attempt_with_the_previous_date_and_only_posts_on_start',async()=>{
  const {port,control}=setup()
  const old=publication({stage:'completed'})
  port.latestInfoxPublication.mockResolvedValue({publication:old} as never)
  port.infoxPublicationHistory.mockResolvedValue({publications:[old]} as never)
  port.infoxPublicationPreview.mockResolvedValue({available:true,charged:false,can_start:true,blocked_reason:null,latest_publication_id:old.id,has_remote_render:true,remote_updated_at:'2026-11-04T00:00:00Z'})
  await control.restore('run-1')
  expect(control.canStart.value).toBe(false)
  await control.prepareNew()
  expect(control.marketDate.value).toBe('2026-11-04')
  expect(port.rerenderInfoxPublication).not.toHaveBeenCalled()
  control.marketDate.value='2026-11-03'
  await control.confirmReview(true)
  await Promise.all([control.start(),control.start()])
  expect(port.rerenderInfoxPublication).toHaveBeenCalledTimes(1)
  expect(port.rerenderInfoxPublication.mock.calls[0]![1]).toMatchObject({market_date:'2026-11-03',previous_publication_id:'pub-1'})
  expect(port.startInfoxPublication).not.toHaveBeenCalled()
  expect(control.history.value.map(r=>r.id)).toEqual(['pub-2','pub-1'])
  control.dispose()
})

it('cancelled_preparation_cannot_be_reenabled_by_a_late_preview',async()=>{
  const {port,control}=setup()
  port.latestInfoxPublication.mockResolvedValue({publication:publication({stage:'completed'})} as never)
  await control.restore('run-1')
  let finish!:(x:unknown)=>void
  port.infoxPublicationPreview.mockImplementation(()=>new Promise(r=>{finish=r}) as never)
  const pending=control.prepareNew()
  await vi.waitFor(()=>expect(port.infoxPublicationPreview).toHaveBeenCalled())
  control.cancelPreparation()
  finish({available:true,charged:false,can_start:true,latest_publication_id:'pub-1'})
  await pending
  expect(control.preparing.value).toBe(false)
  expect(control.canStart.value).toBe(false)
  control.dispose()
})

it('lost_rerender_response_does_not_unlock_the_old_record_or_repost',async()=>{
  const {port,control}=setup()
  port.latestInfoxPublication.mockResolvedValue({publication:publication({stage:'failed'})} as never)
  port.infoxPublicationPreview.mockResolvedValue({available:true,charged:false,can_start:true,blocked_reason:null,latest_publication_id:'pub-1',has_remote_render:true,remote_updated_at:'2026-11-04T00:00:00Z'})
  await control.restore('run-1');await control.prepareNew()
  port.rerenderInfoxPublication.mockRejectedValue(new Error('lost'))
  await control.confirmReview(true); await control.start();await control.confirmReview(true); await control.start()
  expect(control.uncertain.value).toBe(true)
  expect(control.canPrepare.value).toBe(false)
  expect(port.rerenderInfoxPublication).toHaveBeenCalledTimes(1)
  control.dispose()
})

it('a_late_preparation_preview_cannot_override_lost_editor_eligibility',async()=>{
  const {port,control}=setup()
  port.latestInfoxPublication.mockResolvedValue({publication:publication({stage:'completed'})} as never)
  await control.restore('run-1')
  let finish!:(x:unknown)=>void
  port.infoxPublicationPreview.mockImplementation(()=>new Promise(r=>{finish=r}) as never)
  const pending=control.prepareNew()
  await vi.waitFor(()=>expect(port.infoxPublicationPreview).toHaveBeenCalled())
  await control.setEligibility(false)
  finish({available:true,charged:false,can_start:true,latest_publication_id:'pub-1'})
  await pending
  expect(control.canStart.value).toBe(false)
  control.dispose()
})
