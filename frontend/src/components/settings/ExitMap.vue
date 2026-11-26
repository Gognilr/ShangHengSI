<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ModelOverview, ModelRow } from '@/api/admin'
import { useFx, useLitOnce } from '@/utils/motion'

/**
 * 出口地图（澄明二版 6.20 ①，地图尺度的往返线）：本机地带 → 网关四关（与 gateway.py 的顺序一致）→ 各云端模型节点。
 * 线的粗细按本月调用量 2～10px 线性取值，停用的模型画虚线；默认模型带星点。首次进入视口时节点依次点亮（第五节）。
 * 本地部分只显示状态，不显示任何任务内容。第一关可点，由页面就地修改个人每日调用上限。
 */
const props = defineProps<{ overview: ModelOverview | null; models: ModelRow[] }>()
const emit = defineEmits<{ cap: [] }>()
defineExpose({ ping, flash, replay })

const LOCAL = [
  { id: 'ner', label: '名称识别', name: 'GLiNER' },
  { id: 'embedding', label: '语义检索', name: 'bge-m3' },
  { id: 'asr', label: '录音转写', name: 'FireRed 一组' },
  { id: 'mt', label: '本地翻译', name: 'Hy-MT2' },
] as const
const STATE = { enabled: '运行中', disabled: '未启用', unavailable: '不可用' } as const

const wrap = ref<HTMLElement>()
const svg = ref<SVGSVGElement>()
const lit = useLitOnce(wrap)
const fx = useFx()
const run = ref(0) // 重放时换 key，让点亮动画再走一遍
const flashing = ref<number | null>(null)

const local = computed(() => LOCAL.map(l => {
  const status = props.overview?.local.find(x => x.id === l.id)?.status ?? 'unavailable'
  return { ...l, status, on: status === 'enabled' }
}))
const usage = (id: number) => props.overview?.models.find(m => m.id === id)
const nodes = computed(() => {
  const max = Math.max(1, ...props.models.map(m => usage(m.id)?.calls ?? 0))
  return props.models.map((m, i) => {
    const calls = usage(m.id)?.calls ?? 0
    const def = [m.is_default_review && '审核默认', m.is_default_assist && '助手默认'].filter(Boolean).join('、')
    return { id: m.id, i, name: m.display_name, off: !m.enabled, calls, def, w: m.enabled ? 2 + calls / max * 8 : 2 }
  })
})
// 节点多于三个时画布加高，网关一线始终居中
const step = computed(() => nodes.value.length <= 3 ? 70 : 58)
const H = computed(() => Math.max(300, (nodes.value.length - 1) * step.value + 48 + 110))
const CY = computed(() => H.value / 2)
const nodeY = (i: number) => CY.value - ((nodes.value.length - 1) * step.value + 48) / 2 + i * step.value
const localY = (i: number) => CY.value - 108 + i * 54
const cloudTop = computed(() => Math.min(CY.value - 135, nodeY(0) - 41))
const cloudH = computed(() => Math.max(CY.value + 135, nodeY(nodes.value.length - 1) + 60) - cloudTop.value)

const gates = computed(() => {
  const o = props.overview
  const cap = o?.user_daily_cap ?? 0
  const t = o?.total
  const rate = t && t.calls + t.failed > 0 ? `成功率 ${(t.calls / (t.calls + t.failed) * 100).toFixed(1)}%` : '本月尚无调用'
  const allUnpriced = !!t && t.calls > 0 && t.unpriced === t.calls
  return [
    { n: 1, name: '调用上限', a: cap ? `每人每天 ${cap} 次` : '每人每天不限', b: '点此修改', hot: false, edit: true },
    { n: 2, name: '发送前复检', a: `本月拦截 ${t?.blocked ?? 0} 次`, b: t?.blocked ? '没遮住的未发出' : '', hot: !!t?.blocked, edit: false },
    { n: 3, name: '发送', a: `本月 ${t?.calls ?? 0} 次`, b: rate, hot: false, edit: false },
    { n: 4, name: '记录在案', a: allUnpriced ? '未收录价格' : `本月 ¥ ${t?.cost ?? '0.00'}`, b: t?.unpriced && !allUnpriced ? `${t.unpriced} 次未收录价格` : '', hot: false, edit: false },
  ]
})
const label = computed(() => `出口地图：本机 ${local.value.filter(l => l.on).length} 个本地模型在运行；网关四关，本月拦截 ${props.overview?.total.blocked ?? 0} 次；云端 ${nodes.value.length} 个模型`)

function replay() { run.value++ }
/** 体检时光点从网关走到该节点（只在完整档）。 */
function ping(id: number) {
  const dot = svg.value?.querySelector<SVGCircleElement>('.ping')
  const node = nodes.value.find(n => n.id === id)
  if (!dot || !node || fx.value !== 'full') return
  const y = nodeY(node.i) + 24
  dot.animate([{ transform: `translate(722px, ${CY.value}px)`, opacity: 1 }, { transform: `translate(756px, ${y}px)`, opacity: 1, offset: .85 }, { transform: `translate(756px, ${y}px)`, opacity: 0 }],
    { duration: 900, easing: 'cubic-bezier(.22,1,.36,1)' })
}
/** 更换默认模型后对应节点闪一下。 */
function flash(id: number) { flashing.value = null; requestAnimationFrame(() => { flashing.value = id; setTimeout(() => { if (flashing.value === id) flashing.value = null }, 1000) }) }
/** 节点宽 132，名称按字宽估算截断（汉字约 12.5px，拉丁约 7px）。 */
function clip(name: string, max = 104) {
  let w = 0
  for (let i = 0; i < name.length; i++) { w += name.charCodeAt(i) < 128 ? 7 : 12.5; if (w > max) return name.slice(0, i - 1) + '…' }
  return name
}
const STAR = 'M6 0c.3 2.2 1 3 3.3 3.3C7 3.6 6.3 4.4 6 6.6 5.7 4.4 5 3.6 2.7 3.3 5 3 5.7 2.2 6 0z'
</script>

<template>
  <div ref="wrap" class="exit-map">
    <svg ref="svg" :key="run" class="xm" :class="{ armed: fx === 'full', go: lit }" :viewBox="`0 0 900 ${H}`" :style="{ height: `${H}px` }" role="img" :aria-label="label">
      <!-- 本机地带 -->
      <rect class="zbox-l" x="0" :y="CY - 135" width="210" height="270" rx="14" />
      <text class="zt l" x="16" :y="CY - 111">本机 · 原件不出内网</text>
      <g v-for="(l, i) in local" :key="l.id" class="lm lit" :class="{ off: !l.on }" :style="{ '--i': i }">
        <rect x="14" :y="localY(i)" width="182" height="42" rx="9" />
        <circle cx="32" :cy="localY(i) + 21" r="4" />
        <text x="46" :y="localY(i) + 18">{{ l.label }}</text>
        <text class="s" x="46" :y="localY(i) + 33">{{ l.name }} · {{ STATE[l.status] }}</text>
      </g>
      <path class="pipe lit" style="--i:4" :d="`M210 ${CY}H246`" />
      <!-- 网关四关 -->
      <text class="zt g" x="250" :y="CY - 59">模型网关 · 依次经过四关</text>
      <g v-for="(g, i) in gates" :key="g.n" class="gate lit" :class="{ hot: g.hot, edit: g.edit }" :style="{ '--i': 5 + i }"
         :role="g.edit ? 'button' : undefined" :tabindex="g.edit ? 0 : undefined" :aria-label="g.edit ? `调用上限：${g.a}，修改` : undefined"
         @click="g.edit && emit('cap')" @keydown.enter="g.edit && emit('cap')">
        <rect :x="250 + i * 118" :y="CY - 51" width="106" height="104" rx="10" />
        <text class="gn" :x="262 + i * 118" :y="CY - 27">{{ g.n }}</text>
        <text :x="262 + i * 118" :y="CY - 3">{{ g.name }}</text>
        <text class="s" :class="{ w: g.hot }" :x="262 + i * 118" :y="CY + 20">{{ g.a }}</text>
        <text v-if="g.b" class="s" :class="{ w: g.hot, lk: g.edit }" :x="262 + i * 118" :y="CY + 37">{{ g.b }}</text>
        <path v-if="i < 3" class="pipe" :d="`M${356 + i * 118} ${CY}H${368 + i * 118}`" />
      </g>
      <path class="pipe m lit" style="--i:9" :d="`M710 ${CY}H722`" />
      <!-- 云端地带 -->
      <rect class="zbox-m" x="744" :y="cloudTop" width="156" :height="cloudH" rx="14" />
      <text class="zt m" x="760" :y="cloudTop + 24">云端 · 只见代号</text>
      <template v-for="n in nodes" :key="n.id">
        <path class="wire lit" :class="{ off: n.off }" :style="{ '--i': 10 + n.i, strokeWidth: n.w }" :d="`M722 ${CY}C736 ${CY} 736 ${nodeY(n.i) + 24} 756 ${nodeY(n.i) + 24}`" />
        <g class="node lit" :class="{ off: n.off, flash: flashing === n.id }" :style="{ '--i': 11 + n.i }">
          <rect x="756" :y="nodeY(n.i)" width="132" height="48" rx="10" />
          <text x="770" :y="nodeY(n.i) + 21">{{ clip(n.name) }}<title>{{ n.name }}</title></text>
          <text class="s" x="770" :y="nodeY(n.i) + 37">{{ n.off ? '已停用' : `${n.calls} 次` }}</text>
          <path v-if="n.def" class="star" :d="STAR" :transform="`translate(870 ${nodeY(n.i) + 8})`"><title>{{ n.def }}</title></path>
        </g>
      </template>
      <circle class="ping" r="5" cx="0" cy="0" opacity="0" />
    </svg>
    <div class="xm-foot">
      <span><i />线越粗，本月调用越多</span><span><i class="d" />已停用</span>
      <span><svg class="st" viewBox="0 0 12 7" aria-hidden="true"><path :d="STAR" /></svg>默认模型（悬停看用途）</span>
      <span class="r">本地部分只显示状态，不显示任何任务内容</span>
    </div>
  </div>
</template>

<style scoped>
.exit-map { position: relative; }
.xm { display: block; width: 100%; overflow: visible; }
.zbox-l { fill: var(--si-zone-l); stroke: color-mix(in srgb, var(--si-brand-line) 60%, transparent); }
.zbox-m { fill: var(--si-zone-m); stroke: color-mix(in srgb, var(--si-ai-line) 70%, transparent); }
.zt { font: 600 13px var(--si-font-sans); }
.zt.l { fill: var(--si-blue-ink); }
.zt.g { fill: var(--si-ink-2); }
.zt.m { fill: var(--si-ai-ink); }
text { font-family: var(--si-font-sans); font-size: 12.5px; fill: var(--si-ink-1); }
text.s { font-size: 11.5px; fill: var(--si-ink-3); }
text.w { fill: var(--si-warn-ink); }
text.lk { fill: var(--si-blue-ink); }
.lm rect { fill: var(--si-surface-1); stroke: var(--si-hair); }
.lm circle { fill: var(--si-trip-l); }
.lm.off rect { stroke-dasharray: 3 3; }
.lm.off text { fill: var(--si-ink-4); }
.lm.off circle { fill: var(--si-trip-idle); }
.gate rect { fill: var(--si-surface-1); stroke: var(--si-hair-strong); transition: stroke var(--si-d2) var(--si-ease-out); }
.gate .gn { font: 600 11px var(--si-font-mono); fill: var(--si-blue-ink); }
.gate.hot rect { stroke: var(--si-warn); stroke-width: 1.5; }
.gate.edit { cursor: pointer; outline: none; }
.gate.edit:hover rect, .gate.edit:focus-visible rect { stroke: var(--si-brand-2); }
.gate.edit:focus-visible rect { stroke-width: 2; }
.pipe { fill: none; stroke: var(--si-trip-l); stroke-width: 2.5; }
.pipe.m { stroke: var(--si-trip-m); }
.wire { fill: none; stroke: var(--si-trip-m); stroke-linecap: round; opacity: .55; }
.wire.off { stroke: var(--si-trip-idle); stroke-dasharray: 4 5; opacity: .8; }
.node rect { fill: var(--si-surface-1); stroke: var(--si-ai-line); }
.node.off rect { stroke: var(--si-hair); stroke-dasharray: 3 3; }
.node.off text { fill: var(--si-ink-4); }
.node .star { fill: var(--si-ai); }
.node.flash rect { animation: xm-flash 1s var(--si-ease-out); }
.ping { fill: var(--si-trip-l); }
@keyframes xm-flash { 0%, 100% { fill: var(--si-surface-1); } 30% { fill: var(--si-ai-soft); stroke: var(--si-ai); } }
/* 地图点亮：只在完整档依次走一遍（armed）；精简、关闭档直接显示终态 */
.armed .lit { opacity: 0; }
.armed.go .lit { animation: xm-lit .5s var(--si-ease-out) forwards; animation-delay: calc(var(--i) * 110ms); }
@keyframes xm-lit { from { opacity: 0; } to { opacity: 1; } }
.xm-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; margin-top: 6px; font: var(--si-font-meta); color: var(--si-ink-3); }
.xm-foot i { display: inline-block; width: 18px; margin-right: 6px; border-top: 2px solid var(--si-trip-m); vertical-align: middle; }
.xm-foot i.d { border-top-style: dashed; border-top-color: var(--si-trip-idle); }
.xm-foot .st { width: 12px; height: 7px; margin-right: 5px; fill: var(--si-ai); transform: scale(1.6); }
.xm-foot .r { margin-left: auto; }
</style>
