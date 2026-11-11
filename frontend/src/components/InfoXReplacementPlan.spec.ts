import { cleanup, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'
import InfoXReplacementPlan from './InfoXReplacementPlan.vue'
afterEach(cleanup)

describe('InfoXReplacementPlan', () => {
  it('기업명 포함 즐겨찾기 선택과 서버 미저장 계획을 구분한다', () => {
    render(InfoXReplacementPlan, { props: { plan: { state: 'ready', selections: [{
      target: { scene_index: 0, layer_id: 'media', item_index: 1 }, slot_frames: 360,
      candidate: { id: 'candidate-1', title: '삼성전자 반도체', video_id: 'source-1', bucket: 'clips',
        segment: 'chosen.mp4', frame_index: 9, timestamp: 54.054, duration: 6.01 },
    }], checks: { scope_preserved: true, normal_assets_preserved: true, layout_preserved: true,
      timing_preserved: true, references_consistent: true, old_active_references_removed: true } } } })
    expect(screen.getByText('교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.')).toBeVisible()
    expect(screen.getByText(/삼성전자 반도체/)).toBeVisible()
    expect(screen.getByText('슬롯 길이에 맞춘 반복 계획입니다. 실제 재생은 저장 단계에서 확인합니다.')).toBeVisible()
    expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument()
  })

  it('shows target and clip counts, sequence timing, and a short candidate pool', () => {
    const candidate = {id:'a',title:'중국 대만 반도체',video_id:'s',bucket:'b',segment:'a.mp4',
      frame_index:0,timestamp:0,duration:6}
    render(InfoXReplacementPlan, {props:{plan:{state:'ready',selections:[{
      target:{scene_index:0,layer_id:'media',item_index:null},candidate,slot_frames:420,
      available_candidates:2,clips:[
        {candidate,slot_frames:140,result_item_index:0},
        {candidate:{...candidate,id:'b',segment:'b.mp4'},slot_frames:140,result_item_index:1},
        {candidate,slot_frames:140,result_item_index:2},
      ],
    }]}}})
    expect(screen.getByText('대체 대상 1개 · 배치 클립 3개 · 서로 다른 에셋 2개')).toBeVisible()
    expect(screen.getByText(/후보 부족.*2개.*순환/)).toBeVisible()
    expect(screen.getByText(/1번.*4.667초/)).toBeVisible()
    expect(screen.queryByText('슬롯 길이에 맞춘 반복 계획입니다. 실제 재생은 저장 단계에서 확인합니다.')).not.toBeInTheDocument()
  })
})
