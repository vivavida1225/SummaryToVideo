import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import InfoXAssetSave from './InfoXAssetSave.vue'

describe('InfoXAssetSave', () => {
  it.each([
    ['checking_source', '최신 서버 상태와 고정한 교체 대상을 확인하는 중…'],
    ['saving', '편집 데이터를 저장하는 중…'],
    ['verifying', '서버를 다시 읽어 적용·보존 결과를 확인하는 중…'],
    ['saved_verified', '서버 저장·재조회 검증 완료'],
    ['unchanged_verified', '무변경 재조회 검증 완료 · 서버 저장 없음'],
  ])('단계 %s 표시와 실제 재생 미확인을 구분한다', (state, label) => {
    render(InfoXAssetSave, { props: { save: { state, receipt_state: 'not_sent' } } })
    expect(screen.getByText(label)).toBeVisible()
    if (state === 'saved_verified') expect(screen.getByText(/실제 편집기의 재생 확인은 별도/)).toBeVisible()
  })
  it('응답 유실의 불확실성과 충돌 원인을 표시한다', () => {
    render(InfoXAssetSave, { props: { save: { state: 'needs_attention', receipt_state: 'unknown', error_code: 'version_conflict' } } })
    expect(screen.getByText(/다른 편집의 서버 버전/)).toBeVisible()
    expect(screen.getByText(/저장을 재전송하지 않고/)).toBeVisible()
  })
})
