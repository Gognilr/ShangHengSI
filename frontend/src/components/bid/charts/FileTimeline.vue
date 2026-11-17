<script setup lang="ts">
import { computed, ref } from 'vue'
import type { BidTimelineFile } from '@/api/bid'
import { fmtTime, scale, useWidth } from './chart'

// 文件时间线（甘特图，V34Q-27 第三部分第 4 条）：每家一行，各文件“创建—最后修改”画成横条，开标日一条竖线。
// 不同投标人的文件作者或文件标识相同、或同一软件 10 分钟内创建（与“文件属性相同”线索同一口径，后端分组）时用同色描边。
const props = defineProps<{ files: BidTimelineFile[]; rows: { id: string | null; label: string; name: string }[]; opening: string }>()
const box = ref<HTMLElement>(), width = useWidth(box)
const LABEL = 104, R = 20, LANE = 16, ROWGAP = 10, TOP = 6, AXIS_H = 26
const DAY = 86400000, MAX_SPAN = 120 * DAY
const t = (s: string | null) => (s ? new Date(s).getTime() : NaN)
// 开标日按北京时间当天结束
const openAt = computed(() => new Date(`${props.opening}T23:59:59+08:00`).getTime())
const groupColor = ['var(--si-accent)', 'var(--si-blue-ink)', 'var(--si-accent-2)', 'var(--si-ink-2)']
const color = (g: number | null) => (g ? groupColor[(g - 1) % groupColor.length]! : null)

const domain = computed(() => {
  const all = props.files.flatMap(f => [t(f.created), t(f.modified)]).filter(v => !Number.isNaN(v))
  let hi = Math.max(...all, openAt.value), lo = Math.min(...all)
  // 模板复制来的文件常带着几年前的创建时间：只画开标前后约 120 天，更早的起点画成左侧开口
  const clipped = hi - lo > MAX_SPAN
  if (clipped) lo = Math.min(...all.filter(v => v >= hi - MAX_SPAN), hi - DAY)
  const pad = (hi - lo) * 0.03 || DAY / 4
  return { lo: lo - pad, hi: hi + pad, clipped }
})
const x = computed(() => scale(domain.value.lo, domain.value.hi, LABEL, width.value - R))
const layout = computed(() => {
  let y = TOP
  return props.rows.map(row => {
    const list = props.files.filter(f => f.bidder_id === row.id)
    const top = y
    y += Math.max(1, list.length) * LANE + ROWGAP
    return { ...row, top, height: Math.max(1, list.length) * LANE, bars: list.map((f, k) => {
      const c = t(f.created), m = t(f.modified), s = Number.isNaN(c) ? m : c, e = Number.isNaN(m) ? c : Math.max(m, s)
      const x0 = x.value(Math.max(s, domain.value.lo)), x1 = Math.max(x0 + 3, x.value(e))
      return { f, y: top + k * LANE + 3, x0, x1, cut: s < domain.value.lo, gone: e < domain.value.lo, since: s, color: color(f.group), point: Number.isNaN(c) || Number.isNaN(m) }
    }) }
  }).filter(r => r.bars.length)
})
const H = computed(() => { const last = layout.value[layout.value.length - 1]; return (last ? last.top + last.height + ROWGAP : TOP) + AXIS_H })
const unit = computed<'hour' | 'day' | 'month'>(() => { const span = domain.value.hi - domain.value.lo; return span <= 2 * DAY ? 'hour' : span <= 150 * DAY ? 'day' : 'month' })
const ticks = computed(() => {
  const { lo, hi } = domain.value, span = hi - lo, n = Math.max(2, Math.floor((width.value - LABEL) / 110))
  const steps = [3600e3, 3 * 3600e3, 6 * 3600e3, 12 * 3600e3, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY]
  const step = steps.find(s => span / s <= n) ?? 30 * DAY
  // 按北京时间对齐整点 / 整天
  const off = 8 * 3600e3, first = Math.ceil((lo + off) / Math.min(step, DAY)) * Math.min(step, DAY) - off
  const out: number[] = []
  for (let v = first; v <= hi; v += step) out.push(v)
  return out
})
const groups = computed(() => [...new Set(props.files.map(f => f.group).filter((g): g is number => !!g))])
const fmt = (s: string | null) => (s ? fmtTime(t(s), 'full') : '未读到')
const caption = computed(() => {
  const parts: string[] = []
  if (groups.value.length) parts.push(`${groups.value.length} 组文件来自不同投标人且作者、文件标识相同或创建时间相近（同色描边），与“文件属性相同”线索对应`)
  else parts.push('各家文件之间未见作者相同或创建时间相近')
  const late = props.files.filter(f => f.bidder_id && t(f.modified) > openAt.value).length
  if (late) parts.push(`${late} 个文件的最后修改时间晚于开标日，请核实`)
  if (domain.value.clipped) parts.push('更早创建的文件（多为模板复制）左端开口，标出创建日期')
  return parts.join('；') + '。'
})
</script>

<template>
  <figure ref="box" class="chart">
    <svg :width="width" :height="H" role="img" :aria-label="caption">
      <g v-for="v in ticks" :key="v">
        <line class="grid" :x1="x(v)" :x2="x(v)" :y1="0" :y2="H - AXIS_H + 4" />
        <text :x="x(v)" :y="H - 8" text-anchor="middle">{{ fmtTime(v, unit) }}</text>
      </g>
      <g v-for="row in layout" :key="row.id ?? 'tender'">
        <line class="axis" :x1="0" :x2="width - R" :y1="row.top + row.height + ROWGAP / 2" :y2="row.top + row.height + ROWGAP / 2" style="stroke: var(--si-hair-faint)" />
        <text :x="0" :y="row.top + 12" :class="{ strong: !!row.id }" style="font: var(--si-font-label-sm)">{{ row.label }}<title>{{ row.name }}</title></text>
        <g v-for="b in row.bars" :key="b.f.file_id">
          <title>{{ b.f.file }}：创建 {{ fmt(b.f.created) }} · 最后修改 {{ fmt(b.f.modified) }}{{ b.f.group ? ` · 第 ${b.f.group} 组` : '' }}</title>
          <rect :x="b.x0" :y="b.y" :width="b.x1 - b.x0" height="9" rx="2"
            :style="b.color ? { fill: `color-mix(in srgb, ${b.color} 28%, transparent)`, stroke: b.color, strokeWidth: 1.5 } : { fill: row.id ? 'var(--si-brand-2)' : 'var(--si-ink-5)', opacity: 0.55 }" />
          <text v-if="b.gone" :x="b.x0 + 10" :y="b.y + 8">{{ fmtTime(b.since, 'full').slice(0, 10) }}</text>
          <path v-if="b.cut" :d="`M${b.x0 + 5},${b.y - 1} L${b.x0},${b.y + 4.5} L${b.x0 + 5},${b.y + 10}`" fill="none" style="stroke: var(--si-ink-3); stroke-width: 1.2" />
        </g>
      </g>
      <line class="limit" :x1="x(openAt)" :x2="x(openAt)" :y1="0" :y2="H - AXIS_H + 4" style="stroke: var(--si-ink-1); stroke-dasharray: 4 3" />
      <text class="halo strong" :x="x(openAt) - 4" :y="10" text-anchor="end">开标</text>
    </svg>
    <figcaption>{{ caption }}</figcaption>
    <div v-if="groups.length" class="legend" aria-hidden="true">
      <span v-for="g in groups" :key="g"><i :style="{ border: `1.5px solid ${color(g)}`, background: `color-mix(in srgb, ${color(g)} 28%, transparent)` }" />第 {{ g }} 组</span>
    </div>
    <table class="sr-only">
      <caption>各文件的创建与最后修改时间</caption>
      <tr><th>投标人</th><th>文件</th><th>创建</th><th>最后修改</th><th>同组</th></tr>
      <template v-for="row in layout" :key="row.id ?? 'tender'">
        <tr v-for="b in row.bars" :key="b.f.file_id"><td>{{ row.name }}</td><td>{{ b.f.file }}</td><td>{{ fmt(b.f.created) }}</td><td>{{ fmt(b.f.modified) }}</td><td>{{ b.f.group ?? '' }}</td></tr>
      </template>
    </table>
  </figure>
</template>
