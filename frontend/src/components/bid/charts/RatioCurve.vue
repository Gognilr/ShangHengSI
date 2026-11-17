<script setup lang="ts">
import { computed, ref } from 'vue'
import { amount, scale, useWidth } from './chart'

// 分项比值曲线（V34Q-27 第三部分第 2 条）：横轴为分项（按对齐顺序），纵轴为两家单价之比（高的一家比低的一家）。
// 有规律的各对画在同一张图上，每对一条线；比值恒定时近似水平线，一眼可见“按同一比例算出”。比值只用于画图与显示，不参与金额计算。
const props = defineProps<{
  items: { name: string; prices: Record<string, string | null>; fixed: boolean }[]
  pairs: { a: string; b: string }[]; labels: Record<string, string>
}>()
const box = ref<HTMLElement>(), width = useWidth(box)
const L = 64, R = 132, T = 12, B = 136, H = 168
const tone = ['var(--si-brand)', 'var(--si-accent)', 'var(--si-ink-2)', 'var(--si-brand-2)', 'var(--si-accent-2)', 'var(--si-ink-4)']
const f = (r: number, d = 4) => r.toFixed(d).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
const live = computed(() => props.items.map((it, i) => ({ i, name: it.name || '（无品名）', it })).filter(x => !x.it.fixed))
const series = computed(() => props.pairs.map((p, k) => {
  const raw = live.value.map((x, j) => ({ j, name: x.name, pa: x.it.prices[p.a], pb: x.it.prices[p.b] })).filter(x => amount(x.pa) > 0 && amount(x.pb) > 0)
  // 方向：取高的一家比低的一家（与后端 mean_ratio 同向）
  const flip = raw.reduce((s, x) => s + amount(x.pb) / amount(x.pa), 0) < raw.length
  const pts = raw.map(x => ({ ...x, r: flip ? amount(x.pa) / amount(x.pb) : amount(x.pb) / amount(x.pa) }))
  const rs = pts.map(x => x.r)
  return { key: p.a + p.b, a: p.a, b: p.b, hi: flip ? p.a : p.b, lo: flip ? p.b : p.a, pts, color: tone[k % tone.length]!,
    min: Math.min(...rs), max: Math.max(...rs), mean: rs.reduce((s, r) => s + r, 0) / (rs.length || 1) }
}).filter(s => s.pts.length >= 2))
// 纵轴：各对比值的范围，至少展开 ±3%，比值恒定时线才显得“平”
const y = computed(() => {
  const lo = Math.min(...series.value.map(s => s.min)), hi = Math.max(...series.value.map(s => s.max)), mid = (lo + hi) / 2
  const half = Math.max((hi - lo) / 2, mid * 0.03) * 1.2
  return { s: scale(mid - half, mid + half, B, T), ticks: [mid - half / 1.2, mid, mid + half / 1.2] }
})
const n = computed(() => live.value.length)
const x = computed(() => scale(0, Math.max(1, n.value - 1), L, width.value - R))
const path = (s: (typeof series.value)[number]) => s.pts.map((p, k) => `${k ? 'L' : 'M'}${x.value(p.j).toFixed(1)},${y.value.s(p.r).toFixed(1)}`).join('')
// 线尾标签：按比值上下排开，避免重叠
const tags = computed(() => {
  const list = series.value.map(s => ({ key: s.key, color: s.color, text: `${props.labels[s.hi]} ÷ ${props.labels[s.lo]}`, y: y.value.s(s.pts[s.pts.length - 1]!.r) })).sort((p, q) => p.y - q.y)
  list.forEach((t, k) => { if (k && t.y - list[k - 1]!.y < 14) t.y = list[k - 1]!.y + 14 })
  return list
})
const caption = computed(() => series.value.length > 2 ? (() => {
  const wave = Math.max(...series.value.map(s => (s.max - s.min) / s.mean / 2)) * 100
  return `${series.value.length} 对的分项价格之比各自` + (wave < 0.005 ? '恒定' : `在平均值 ±${f(wave, 2)}% 以内`) + '，每条线都近似水平，像按同一比例算出；悬停线尾名称与圆点可看各项比值。'
})() : series.value.map(s => {
  const who = `${props.labels[s.hi]} ÷ ${props.labels[s.lo]}`
  return s.max - s.min < 1e-9 ? `${who} ${s.pts.length} 项之比都是 ${f(s.mean)}` : `${who} ${s.pts.length} 项之比在 ${f(s.min, 3)}～${f(s.max, 3)} 之间`
}).join('；') + `；${series.value.length > 1 ? '每条线' : '这条线'}越平，越像按同一比例算出。`)
</script>

<template>
  <figure ref="box" class="chart">
    <svg v-if="series.length" :width="width" :height="H" role="img" :aria-label="caption">
      <g v-for="t in y.ticks" :key="t">
        <line class="grid" :x1="L" :x2="width - R" :y1="y.s(t)" :y2="y.s(t)" />
        <text :x="L - 8" :y="y.s(t) + 4" text-anchor="end">{{ f(t, 3) }}</text>
      </g>
      <g v-for="s in series" :key="s.key">
        <path :d="path(s)" fill="none" :style="{ stroke: s.color, strokeWidth: 1.5 }" />
        <circle v-for="p in s.pts" :key="p.j" :cx="x(p.j)" :cy="y.s(p.r)" r="3" :style="{ fill: s.color }"><title>{{ p.name }}：{{ labels[s.a] }} {{ p.pa }} · {{ labels[s.b] }} {{ p.pb }}，比值 {{ f(p.r) }}</title></circle>
      </g>
      <text v-for="t in tags" :key="t.key" :x="width - R + 10" :y="t.y + 4" :style="{ fill: t.color }">{{ t.text }}</text>
      <text :x="L" :y="H - 6">第 1 项</text>
      <text :x="width - R" :y="H - 6" text-anchor="end">第 {{ n }} 项{{ n < items.length ? '（不含给定价的分项）' : '' }}</text>
    </svg>
    <figcaption v-if="series.length">{{ caption }}</figcaption>
    <table class="sr-only">
      <caption>分项价格之比</caption>
      <template v-for="s in series" :key="s.key">
        <tr v-for="p in s.pts" :key="p.j"><th scope="row">{{ labels[s.hi] }} ÷ {{ labels[s.lo] }} · {{ p.name }}</th><td>{{ f(p.r) }}</td></tr>
      </template>
    </table>
  </figure>
</template>
