export const DEFAULT_INFOX_TITLE_TEMPLATE = 'MMDD_시황영상'

const monthDay = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit',
})

export function formatInfoxTitle(template: string, date = new Date()): string {
  const parts = monthDay.formatToParts(date)
  const mmdd = ['month', 'day'].map(type => parts.find(part => part.type === type)!.value).join('')
  return template.replaceAll('MMDD', mmdd)
}

export function validInfoxTitleTemplate(template: string): boolean {
  return template.trim().length > 0 && Array.from(template).length <= 200
}
