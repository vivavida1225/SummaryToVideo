import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import InfoXAssetAudit from './InfoXAssetAudit.vue'

describe('InfoXAssetAudit', () => {
  it('확인 불가 항목을 보존하고 다음 단계 진행을 막지 않음을 안내한다', () => {
    render(InfoXAssetAudit, { props: { audit: { state:'completed', issues:[], items:[
      { scene_index:1, layer_id:'media', item_index:null, reference:{kind:'image'},
        verdict:'unknown', reason:'metadata_missing', hits:[], metadata_sources:[] },
    ] } } })
    expect(screen.getByText('확인 불가 항목은 미검출처럼 교체 대상에서 제외하며, 다음 단계 진행을 막지 않습니다.')).toBeVisible()
    expect(screen.getByText('해당 파일의 검사할 메타데이터가 없습니다.')).toBeVisible()
  })
  it('기업명 검출·확인 불가·미검출·즐겨찾기 예외와 근거를 구분한다', () => {
    const base = { scene_index: 0, scene_id: 'scene-1', layer_id: 'media', item_index: 0,
      reference: { kind: 'image', bucket: 'photos', filename: 'assigned.jpg', segment: null },
      hits: [], metadata_sources: [] }
    render(InfoXAssetAudit, { props: { audit: { state: 'needs_attention', audited_at: null, issues: [],
      error_code: 'inspection_incomplete', items: [
        { ...base, verdict: 'detected', reason: 'company_detected', hits: [
          { term: '삼성전자', field: 'tags[0]', source: 'video.scenes[0].rerank_result[0]' }] },
        { ...base, item_index: 1, verdict: 'unknown', reason: 'metadata_missing' },
        { ...base, item_index: 2, verdict: 'not_detected', reason: 'metadata_inspected' },
        { ...base, item_index: 3, verdict: 'favorite_exempt', reason: 'verified_favorite_replacement' },
      ] } } })
    expect(screen.getByText('기업명 검출 1 · 메타데이터 미검출 1 · 확인 불가 1 · 즐겨찾기 예외 1')).toBeVisible()
    expect(screen.getByText('삼성전자 · tags[0]')).toBeVisible()
    expect(screen.getByText('해당 파일의 검사할 메타데이터가 없습니다.')).toBeVisible()
    expect(screen.getByText('검증된 즐겨찾기 교체 이력과 일치합니다.')).toBeVisible()
  })
})
