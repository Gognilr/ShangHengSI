<script setup lang="ts">
import { computed, ref } from 'vue'
import { amount, scale, useWidth } from './chart'

// 报价分布（V34Q-27 第三部分第 1 条）：一条横轴，每家总价一个点；最高、最低标在点下，平均为虚线，填了限价时加限价竖线；异常低价点用警告墨。
// 只画参加比较的各家；金额文字由后端 Decimal 算好，这里的数值只用于定位。
export interface PricePoint { id: string; label: string; name: string; text: string; mark?: 'highest' | 'lowest' | null; low?: boolean }
const props = defineProps<{ points: PricePoint[]; mean: string; ceiling?: string | null; unit: string; caption: string; quoteLabel?: string; limitLabel?: string }>()
const box = ref<HTMLElement>(), width = useWidth(box)
const PAD = 56, AXIS = 62
const H = computed(() => (props.ceiling ? 122 : 104))
const position = (text: string) => amount(text.replace(/%$/, ''))
const values = computed(() => props.points.map(p => position(p.text)))
// 限价离各家很远时不拉长横轴（点会挤在一起），画在右端并注“→”
const far = computed(() => {
  if (!props.ceiling) return false
  const lo = Math.min(...values.value), hi = Math.max(...values.value), c = position(props.ceiling)
  return c > hi + Math.max(hi - lo, hi * 0.02) * 1.5
})
const x = computed(() => {
  const all = [...values.value, ...(props.ceiling && !far.value ? [position(props.ceiling)] : [])]
  const lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.06 || hi * 0.02 || 1
  return scale(lo - pad, hi + pad, PAD, width.value - PAD - (far.value ? 150 : 0))
})
// 名称标在点上方，相互挤占时错开到第二、三层
const dots = computed(() => {
  const list = props.points.map((p, i) => ({ ...p, cx: x.value(values.value[i]!), tier: 0 })).sort((a, b) => a.cx - b.cx)
  const last: number[] = [-1e9, -1e9, -1e9]
  for (const d of list) {
    const w = d.label.length * 12 + 10
    d.tier = Math.max(0, last.findIndex(end => d.cx - w / 2 > end))
    last[d.tier] = d.cx + w / 2
  }
  return list
})
const markText = { highest: '最高', lowest: '最低' } as const
const meanX = computed(() => x.value(position(props.mean)))
const ceilX = computed(() => (!props.ceiling ? null : far.value ? width.value - PAD : x.value(position(props.ceiling))))
</script>

<template>
  <figure ref="box" class="chart">
    <svg :width="width" :height="H" role="img" :aria-label="caption">
      <line class="axis" :x1="PAD - 16" :x2="width - PAD + 16" :y1="AXIS" :y2="AXIS" />
      <g>
        <line class="ref" :x1="meanX" :x2="meanX" :y1="AXIS - 30" :y2="AXIS + 10" />
        <text :x="meanX" :y="AXIS + 38" text-anchor="middle">平均 {{ mean }}<title>参加比较各家的平均{{ quoteLabel ?? '总价' }} {{ mean }} {{ unit }}</title></text>
      </g>
      <g v-if="ceilX !== null">
        <line v-if="far" class="axis" :x1="ceilX - 150" :x2="ceilX - 140" :y1="AXIS + 5" :y2="AXIS - 5" style="stroke: var(--si-ink-3)" />
        <line v-if="far" class="axis" :x1="ceilX - 144" :x2="ceilX - 134" :y1="AXIS + 5" :y2="AXIS - 5" style="stroke: var(--si-ink-3)" />
        <line class="limit" :x1="ceilX" :x2="ceilX" :y1="AXIS - 52" :y2="AXIS + 42" />
        <text class="halo" :x="ceilX" :y="AXIS + 56" :text-anchor="far ? 'end' : 'middle'" style="fill: var(--si-danger-ink)">{{ limitLabel ?? '限价' }} {{ ceiling }}<title>{{ far ? '限值远高于各家报价，横轴在此处截断' : '' }}</title></text>
      </g>
      <g v-for="d in dots" :key="d.id">
        <title>{{ d.name }}：{{ d.text }} {{ unit }}{{ d.mark ? `（${markText[d.mark]}）` : '' }}{{ d.low ? '，异常低价' : '' }}</title>
        <line v-if="d.tier" class="grid" :x1="d.cx" :x2="d.cx" :y1="AXIS - 22 - d.tier * 16" :y2="AXIS - 7" />
        <circle class="dot" :class="{ warn: d.low }" :cx="d.cx" :cy="AXIS" r="5.5" />
        <text class="halo" :class="{ strong: !!d.mark }" :x="d.cx" :y="AXIS - 13 - d.tier * 16" text-anchor="middle">{{ d.label }}</text>
        <text v-if="d.mark" class="halo" :x="d.cx" :y="AXIS + 22" text-anchor="middle">{{ markText[d.mark] }}</text>
      </g>
    </svg>
    <figcaption>{{ caption }}</figcaption>
    <table class="sr-only">
      <caption>各家{{ quoteLabel ?? '投标总价' }}{{ unit ? `（${unit}）` : '' }}</caption>
      <tr v-for="p in points" :key="p.id"><th scope="row">{{ p.name }}</th><td>{{ p.text }}</td><td>{{ p.mark ? markText[p.mark] : '' }}{{ p.low ? ' 异常低价' : '' }}</td></tr>
      <tr><th scope="row">平均</th><td>{{ mean }}</td><td /></tr>
      <tr v-if="ceiling"><th scope="row">最高限价</th><td>{{ ceiling }}</td><td /></tr>
    </table>
  </figure>
</template>
