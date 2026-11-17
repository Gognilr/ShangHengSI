import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/** 结果页图表（V34-B-06d）共用：手写 SVG，按容器实际宽度绘制（文字不随缩放变形），颜色只用令牌。 */
export function useWidth(el: Ref<HTMLElement | undefined>, fallback = 720) {
  const width = ref(fallback)
  let ro: ResizeObserver | undefined
  onMounted(() => {
    if (!el.value) return
    width.value = Math.round(el.value.clientWidth) || fallback
    ro = new ResizeObserver(entries => { const w = Math.round(entries[0]!.contentRect.width); if (w) width.value = w })
    ro.observe(el.value)
  })
  onBeforeUnmount(() => ro?.disconnect())
  return width
}
/** 已格式化的金额 "1,234.50" → 数值，只用于定位画点，不参与任何金额计算 */
export const amount = (s: string | null | undefined) => (s ? Number(s.replace(/[,\s]/g, '')) : NaN)
/** 线性比例尺 */
export function scale(d0: number, d1: number, r0: number, r1: number) {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0)
  return (v: number) => (d1 === d0 ? (r0 + r1) / 2 : r0 + (v - d0) * k)
}
/** 等级 → 图上的色调类名（强：危险墨；中：警告墨；弱：浅墨） */
export const levelClass = (level: string) => (level === 'strong' || level === 'veto' ? 'lv-s' : level === 'medium' ? 'lv-m' : 'lv-w')
const shanghai = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, ...opts })
const md = shanghai({ month: '2-digit', day: '2-digit' }), ym = shanghai({ year: 'numeric', month: '2-digit' }), hm = shanghai({ hour: '2-digit', minute: '2-digit' })
const full = shanghai({ year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
export function fmtTime(t: number, unit: 'hour' | 'day' | 'month' | 'full') {
  const d = new Date(t)
  const f = unit === 'hour' ? hm : unit === 'day' ? md : unit === 'month' ? ym : full
  return f.format(d).replace(/\//g, '-')
}
