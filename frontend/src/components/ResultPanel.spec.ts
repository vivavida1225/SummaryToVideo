import { render, screen } from '@testing-library/vue'
import { expect, it } from 'vitest'
import ResultPanel from './ResultPanel.vue'

it('counts text without line endings and preserves the displayed script', () => {
  render(ResultPanel, { props: {
    title: '대본', eyebrow: '결과', value: '가😀\r\n나\n다', label: '대본',
    copyLabel: '복사', downloadLabel: '다운로드',
  } })
  expect(screen.getByText('4자')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '대본' })).toHaveValue('가😀\n나\n다')
})
