import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import InfoXReplacementPlan from './InfoXReplacementPlan.vue'

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
})
