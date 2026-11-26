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

/** 2026-10-15 17:30（到分钟，列表用）。 */
export function formatDateTimeMinute(value: string | Date | null | undefined): string {
  const full = formatDateTime(value)
  return full === '—' ? full : full.slice(0, 16)
}

const THIS_YEAR = () => formatDate(new Date()).slice(0, 4)

/** 列表用短格式（澄明 8.8）：同一年内省略年份，写作“11-20 14:06”；withTime 为 false 时只写日期“11-20”。 */
export function formatShort(value: string | Date | null | undefined, withTime = true): string {
  const full = formatDateTimeMinute(value)
  if (full === '—') return full
  const text = withTime ? full : full.slice(0, 10)
  return text.startsWith(THIS_YEAR()) ? text.slice(5) : text
}

/** 按天的时间线（澄明二版 6.18）：今天、昨天、本周（周一起）、更早；日期按 Asia/Shanghai 计。 */
export type DayGroup = 'today' | 'yesterday' | 'week' | 'earlier'
const DAY_MS = 86400000
function dayNumber(value: string | Date): number | null {
  const d = parseUtc(value)
  if (!d) return null
  const [y, m, day] = formatDate(d).split('-').map(Number)
  return Math.round(Date.UTC(y!, m! - 1, day!) / DAY_MS)
}
export function dayGroup(value: string | Date, now: Date = new Date()): DayGroup {
  const d = dayNumber(value), t = dayNumber(now)
  if (d == null || t == null) return 'earlier'
  if (d >= t) return 'today'
  if (d === t - 1) return 'yesterday'
  // 1970-01-01 是周四：(t + 3) % 7 为距本周一的天数
  return d >= t - ((t + 3) % 7) ? 'week' : 'earlier'
}
const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
/** “11-24 周一” */
export function formatDayLabel(value: string | Date): string {
  const n = dayNumber(value)
  return n == null ? '—' : `${formatShort(value, false)} ${WEEKDAY[(n + 4) % 7]}`
}
/** “14:20” */
export function formatClock(value: string | Date | null | undefined): string {
  const full = formatDateTimeMinute(value)
  return full === '—' ? full : full.slice(11)
}
