import { afterEach, expect, it, vi } from 'vitest'
import { PublishingControl, formatPublishMetadata, seoulToday } from './infoxPublishing'
import type { PublicationRecord } from './types'

export function publication(overrides: Partial<PublicationRecord> = {}): PublicationRecord {
  return { id:'pub-1', run_id:'run-1', video_id:'video-1', version:0, stage:'rendering', failed_stage:null,
    error_code:null, progress:42, snapshot:{market_date:'2026-11-04', title:'261104 AI 영상 시황',
    description:'2026년 11월 4일 AI 영상 시황', privacy_status:'unlisted'}, uploaded_verified:false,
    share_url:null, youtube_video_id:null, clipboard_state:'not_started', ...overrides }
}
function setup() {
  const port = {
    latestInfoxPublication:vi.fn().mockResolvedValue({publication:null}),
    startInfoxPublication:vi.fn().mockResolvedValue(publication()),
    infoxPublication:vi.fn().mockResolvedValue(publication({stage:'completed'})),
    copyInfoxPublicationLink:vi.fn().mockResolvedValue(publication({stage:'completed'})),
    infoxPublicationPreview:vi.fn().mockResolvedValue({available:true,charged:true}),
  }
  return {port, control:new PublishingControl(port,'2026-11-04')}
}
afterEach(() => vi.useRealTimers())

it('invalid_calendar_dates_do_not_crash_the_preview',()=>{
  expect(formatPublishMetadata('2026-99-99')).toBeNull()
  expect(formatPublishMetadata('2026-02-30')).toBeNull()
})

it('starts_once_and_shows_stage_progress',async () => {
  const {port,control}=setup()
  await control.restore('run-1')
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
  await control.start()
  expect(port.startInfoxPublication.mock.calls[0]![0]).toBe('run-2')
  control.dispose()
})
it('date_and_metadata_stay_frozen_across_midnight',async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T14:59:59Z'))
  const {control}=setup()
  expect(seoulToday()).toBe('2026-11-04')
  await control.restore('run-1'); await control.start()
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
  await control.start(); await control.start()
  expect(port.startInfoxPublication).toHaveBeenCalledTimes(1)
  expect(control.record.value?.stage).toBe('needs_attention')
  control.dispose()
})
