<script setup lang="ts">
import { computed, ref } from 'vue'
import { amount, scale, useWidth } from './chart'

// 分项比值曲线（V34Q-27 第三部分第 2 条）：横轴为分项（按对齐顺序），纵轴为两家单价之比（高的一家比低的一家）。
// 比值恒定时近似一条水平线，一眼可见“按同一比例算出”。比值只用于画图与显示，不参与金额计算。
const props = defineProps<{
  items: { name: string; prices: Record<string, string | null>; fixed: boolean }[]
  a: string; b: string; labels: Record<string, string>
}>()
const box = ref<HTMLElement>(), width = useWidth(box)
const L = 64, R = 24, T = 14, H = 150, B = 118
const raw = computed(() => props.items.map((it, i) => ({ i, name: it.name || '（无品名）', pa: it.prices[props.a], pb: it.prices[props.b], fixed: it.fixed }))
  .filter(p => !p.fixed && amount(p.pa) > 0 && amount(p.pb) > 0))
// 方向：取高的一家比低的一家（与后端 mean_ratio 同向）
const flip = computed(() => raw.value.reduce((s, p) => s + amount(p.pb) / amount(p.pa), 0) < raw.value.length)
const hi = computed(() => (flip.value ? props.a : props.b)), lo = computed(() => (flip.value ? props.b : props.a))
const pts = computed(() => raw.value.map(p => ({ ...p, r: flip.value ? amount(p.pa) / amount(p.pb) : amount(p.pb) / amount(p.pa) })))
const stats = computed(() => {
  const rs = pts.value.map(p => p.r)
  return { min: Math.min(...rs), max: Math.max(...rs), mean: rs.reduce((s, r) => s + r, 0) / (rs.length || 1) }
})
// 纵轴至少展开平均值的 ±3%，比值恒定时线才显得“平”
const y = computed(() => {
  const { min, max, mean } = stats.value
  const half = Math.max(max - mean, mean - min, mean * 0.03) * 1.25
  return scale(mean - half, mean + half, B, T)
})
const x = computed(() => scale(0, Math.max(1, pts.value.length - 1), L, width.value - R))
const path = computed(() => pts.value.map((p, k) => `${k ? 'L' : 'M'}${x.value(k).toFixed(1)},${y.value(p.r).toFixed(1)}`).join(''))
const ticks = computed(() => { const { mean } = stats.value; const t = y.value; return [mean * 0.97, mean, mean * 1.03].map(v => ({ v, y: t(v) })) })
const f = (r: number, d = 4) => r.toFixed(d).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
const caption = computed(() => {
  const { min, max, mean } = stats.value, n = pts.value.length
  const who = `${props.labels[hi.value]} ÷ ${props.labels[lo.value]}`
  if (max - min < 1e-9) return `${who}：${n} 项分项价格之比都是 ${f(mean)}，是一条水平线。`
  return `${who}：${n} 项分项价格之比都在 ${f(min, 3)}～${f(max, 3)} 之间（平均 ${f(mean, 3)}），近似一条水平线，像按同一比例算出。`
})
</script>

<template>
  <figure v-if="pts.length >= 2" ref="box" class="chart">
    <svg :width="width" :height="H" role="img" :aria-label="caption">
      <g v-for="t in ticks" :key="t.v">
        <line :class="t.v === stats.mean ? 'ref' : 'grid'" :x1="L" :x2="width - R" :y1="t.y" :y2="t.y" />
        <text :x="L - 8" :y="t.y + 4" text-anchor="end">{{ f(t.v, 3) }}</text>
      </g>
      <path :d="path" fill="none" style="stroke: var(--si-brand); stroke-width: 1.5" />
      <circle v-for="(p, k) in pts" :key="p.i" class="dot" :cx="x(k)" :cy="y(p.r)" r="3"><title>{{ p.name }}：{{ labels[a] }} {{ p.pa }} · {{ labels[b] }} {{ p.pb }}，比值 {{ f(p.r) }}</title></circle>
      <text :x="L" :y="H - 8">第 1 项</text>
      <text :x="width - R" :y="H - 8" text-anchor="end">第 {{ pts.length }} 项（不含给定价）</text>
    </svg>
    <figcaption>{{ caption }}</figcaption>
    <table class="sr-only">
      <caption>{{ labels[hi] }} 与 {{ labels[lo] }} 的分项价格之比</caption>
      <tr v-for="p in pts" :key="p.i"><th scope="row">{{ p.name }}</th><td>{{ p.pa }}</td><td>{{ p.pb }}</td><td>{{ f(p.r) }}</td></tr>
    </table>
  </figure>
</template>
