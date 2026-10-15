/** 数据库与接口的时间都是 UTC（ISO 8601），界面一律按 Asia/Shanghai 显示（SPEC 21）。 */

const TIME_ZONE = 'Asia/Shanghai'

const dateTimeFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

function parseUtc(value: string | Date): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  // 没带时区标记的字符串按 UTC 处理
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const d = new Date(hasZone ? value : value + 'Z')
  return Number.isNaN(d.getTime()) ? null : d
}

function join(parts: Intl.DateTimeFormatPart[]): string {
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

/** 2026-10-15 17:30:05；空值或无法解析时返回“—”。 */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const d = parseUtc(value)
  if (!d) return '—'
  const parts = dateTimeFormat.formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  // 部分环境小时可能输出 "24"，统一成 "00"
  const hour = get('hour') === '24' ? '00' : get('hour')
  return `${join(parts)} ${hour}:${get('minute')}:${get('second')}`
}

/** 2026-10-15 */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const d = parseUtc(value)
  if (!d) return '—'
  return join(dateFormat.formatToParts(d))
}
