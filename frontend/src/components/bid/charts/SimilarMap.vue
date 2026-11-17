<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BidSimilarMap } from '@/api/bid'
import { levelClass, scale, useWidth } from './chart'

// 相似段落位置条（V34Q-27 第三部分第 5 条）：两家各一条轴（全部投标文件按句序依次排列，细竖线为文件分界），
// 相同段落在两条轴上的位置用连带对应：连带集中、平行是整章照搬，散开交错是零散雷同。
const props = defineProps<{ map: BidSimilarMap; a: string; b: string; labels: Record<string, string>; fileNames: Record<string, string> }>()
const box = ref<HTMLElement>(), width = useWidth(box, 640)
const LABEL = 72, R = 12, YA = 18, YB = 82, H = 104
const na = computed(() => Math.max(1, props.map.n[props.a] ?? 1)), nb = computed(() => Math.max(1, props.map.n[props.b] ?? 1))
const xa = computed(() => scale(0, na.value, LABEL, width.value - R)), xb = computed(() => scale(0, nb.value, LABEL, width.value - R))
const segs = computed(() => props.map.segs.map(s => {
  const a0 = xa.value(s.a[0]), a1 = Math.max(a0 + 2, xa.value(s.a[1] + 1)), b0 = xb.value(s.b[0]), b1 = Math.max(b0 + 2, xb.value(s.b[1] + 1))
  return { s, a0, a1, b0, b1, cls: levelClass(s.level) }
}))
const pages = (p: [number, number] | null) => (p ? (p[0] === p[1] ? `第 ${p[0]} 页` : `第 ${p[0]}–${p[1]} 页`) : '')
const where = (fid: string, p: [number, number] | null) => `${props.fileNames[fid] ?? '文件'}${p ? ' ' + pages(p) : ''}`
const bounds = (id: string, n: number, sx: (v: number) => number) => (props.map.files[id] ?? []).slice(1).map(([fid, start]) => ({ fid, x: sx(Math.min(start, n)) }))
// 结论：相似部分占各自全文的比例，以及是集中一处还是分散
const caption = computed(() => {
  const list = props.map.segs
  if (!list.length) return ''
  const cover = (k: 'a' | 'b', n: number) => Math.round((list.reduce((s, x) => s + x[k][1] - x[k][0] + 1, 0) / n) * 100)
  const lo = Math.min(...list.map(s => s.a[0])), hi = Math.max(...list.map(s => s.a[1]))
  const span = (hi - lo + 1) / na.value
  const shape = list.length === 1 ? '集中在一处' : span <= 0.2 ? `${list.length} 段集中在全文约 ${Math.max(1, Math.round(span * 100))}% 的范围内，像整段或整章照搬` : `${list.length} 段分散在全文各处，像零散雷同`
  return `相似部分约占${props.labels[props.a]}全文的 ${Math.max(1, cover('a', na.value))}%、${props.labels[props.b]}的 ${Math.max(1, cover('b', nb.value))}%；${shape}。`
})
</script>

<template>
  <figure ref="box" class="chart">
    <svg :width="width" :height="H" role="img" :aria-label="caption">
      <g v-for="(x, k) in segs" :key="k" :class="x.cls">
        <title>约 {{ x.s.chars }} 字：{{ labels[a] }} {{ where(x.s.fa, x.s.pa) }} · {{ labels[b] }} {{ where(x.s.fb, x.s.pb) }}</title>
        <path :d="`M${x.a0},${YA + 5} L${x.a1},${YA + 5} L${x.b1},${YB - 5} L${x.b0},${YB - 5} Z`" stroke="none" opacity="0.16" />
        <rect :x="x.a0" :y="YA - 5" :width="x.a1 - x.a0" height="10" rx="1" stroke="none" />
        <rect :x="x.b0" :y="YB - 5" :width="x.b1 - x.b0" height="10" rx="1" stroke="none" />
      </g>
      <g v-for="[id, y, n, sx] in ([[a, YA, na, xa], [b, YB, nb, xb]] as const)" :key="id">
        <text :x="0" :y="y + 4" class="strong" style="font: var(--si-font-label-sm)">{{ labels[id] }}<title>共 {{ n }} 句（20 字以上）</title></text>
        <line class="axis" :x1="LABEL" :x2="width - R" :y1="y" :y2="y" />
        <line v-for="t in bounds(id, n, sx)" :key="t.fid" class="axis" :x1="t.x" :x2="t.x" :y1="y - 8" :y2="y + 8"><title>{{ fileNames[t.fid] ?? '文件' }} 从这里开始</title></line>
      </g>
      <text :x="LABEL" :y="H - 2">开头</text>
      <text :x="width - R" :y="H - 2" text-anchor="end">结尾</text>
    </svg>
    <figcaption>{{ caption }}</figcaption>
    <table class="sr-only">
      <caption>相似段落在两家文件中的位置</caption>
      <tr v-for="(x, k) in segs" :key="k"><td>约 {{ x.s.chars }} 字</td><td>{{ labels[a] }} {{ where(x.s.fa, x.s.pa) }}</td><td>{{ labels[b] }} {{ where(x.s.fb, x.s.pb) }}</td></tr>
    </table>
  </figure>
</template>
