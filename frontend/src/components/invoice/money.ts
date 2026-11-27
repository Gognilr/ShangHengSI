/** "-1234567.80" → "-1,234,567.80"：按字符串加千分位，不经浮点数（金额由后端用 Decimal 算好） */
export function money(v: string | null | undefined): string {
  if (v == null || v === '') return '—'
  const neg = v.startsWith('-'), body = neg ? v.slice(1) : v
  const [int = '0', frac = '00'] = body.split('.')
  return `${neg ? '-' : ''}${int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${frac.padEnd(2, '0').slice(0, 2)}`
}
