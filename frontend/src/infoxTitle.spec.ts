import { describe, expect, it } from 'vitest'
import { DEFAULT_INFOX_TITLE_TEMPLATE, formatInfoxTitle, validInfoxTitleTemplate } from './infoxTitle'

describe('InfoX title format', () => {
  it('uses Korea month/day across the UTC date boundary and pads zeroes', () => {
    expect(formatInfoxTitle(DEFAULT_INFOX_TITLE_TEMPLATE, new Date('2026-11-01T15:00:00Z'))).toBe('1102_시황영상')
    expect(formatInfoxTitle(DEFAULT_INFOX_TITLE_TEMPLATE, new Date('2026-01-01T15:00:00Z'))).toBe('0102_시황영상')
  })
  it('expands saved formats each day and permits literal titles', () => {
    expect(formatInfoxTitle('MMDD_마감_MMDD', new Date('2026-11-02T15:00:00Z'))).toBe('1103_마감_1103')
    expect(formatInfoxTitle('내 기본 제목', new Date('2026-11-02T15:00:00Z'))).toBe('내 기본 제목')
  })
  it('rejects blank and over-limit formats using the backend character limit', () => {
    expect(validInfoxTitleTemplate('')).toBe(false)
    expect(validInfoxTitleTemplate('  ')).toBe(false)
    expect(validInfoxTitleTemplate('x'.repeat(201))).toBe(false)
    expect(validInfoxTitleTemplate('한'.repeat(200))).toBe(true)
  })
})
