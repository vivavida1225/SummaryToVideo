import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { h } from 'vue'
import InfoXStep from './InfoXStep.vue'
import type { InfoxStep } from '../infoxFlow'

const step = (status: InfoxStep['status']): InfoxStep => ({ key: 'produce', number: 2, title: '자동 제작', status })
const slots = { default: () => h('input', { 'aria-label': '기록 입력' }) }

describe('InfoXStep', () => {
  it('진행 중인 앞 단계 때문에 대기 중인 수기 검토 안내도 펼쳐 볼 수 있다', () => {
    render(InfoXStep, { props: { step: step('waiting'), current: false, forceOpen: true }, slots })
    expect(screen.getByRole('textbox', { name: '기록 입력' })).toBeVisible()
  })
  it('완료 단계는 요약만 보이고, 사용자가 펼친 기록은 상태 갱신 후에도 접지 않는다', async () => {
    const view = render(InfoXStep, { props: { step: step('active'), current: true, summary: '요약 한 줄' }, slots })
    const input = screen.getByRole('textbox', { name: '기록 입력' })
    expect(input).toBeVisible()
    await fireEvent.update(input, '입력 유지')
    await view.rerender({ step: step('done'), current: false, summary: '요약 한 줄' })
    expect(screen.getByText('요약 한 줄')).toBeVisible()
    expect(screen.getByRole('textbox', { hidden: true })).not.toBeVisible()
    await fireEvent.click(screen.getByRole('button', { name: '펼치기 자동 제작' }))
    expect(screen.getByRole('textbox', { name: '기록 입력' })).toHaveValue('입력 유지')
    // A later poll that yields the same or a different finished status keeps the user's choice.
    await view.rerender({ step: step('done'), current: false, summary: '요약 두 줄' })
    expect(screen.getByRole('textbox', { name: '기록 입력' })).toBeVisible()
    expect(screen.getByRole('button', { name: '접기 자동 제작' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('대기 단계는 제목과 대기 상태만 표시하고 오류 단계는 즉시 펼친다', async () => {
    const view = render(InfoXStep, { props: { step: step('waiting'), current: false }, slots })
    expect(screen.getByText('대기')).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { hidden: true })).not.toBeVisible()
    await view.rerender({ step: step('attention'), current: false })
    expect(screen.getByText('확인 필요')).toBeVisible()
    expect(screen.getByRole('textbox', { name: '기록 입력' })).toBeVisible()
  })
})
