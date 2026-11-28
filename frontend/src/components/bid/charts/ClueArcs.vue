<script setup lang="ts">
import { computed, ref } from 'vue'
import { useWidth } from './chart'

// 串标关系图（弧线图）：各家沿一条基线按代号排开，两家之间有串标线索就在上方连一条弧。
// 同一组（连通的几家）排在一起，弧短、不交叉；疑似串通的一组在基线下衬一条红色色带。
// 弧粗按线索条数，颜色按强弱（红：疑似串通，橙：中等线索，灰：弱线索）。悬停看两家之间是哪几类线索，
// 点弧打开这一对的证据，点代号只看这一家。V3.4 的环形图连线挤在一起、标签压线（V36 用户反馈），改为此式。
export interface ArcNode { id: string; code: string; short: string; name: string }
export interface ArcEdge { a: string; b: string; tone: 'severe' | 'medium' | 'weak'; count: number; kinds: string[]; entry: string }
export interface ArcGroup { ids: string[]; severe: boolean }
const props = defineProps<{ nodes: ArcNode[]; edges: ArcEdge[]; groups: ArcGroup[]; focus?: string; active?: string }>()
const emit = defineEmits<{ open: [entry: string]; bidder: [id: string] }>()

const box = ref<HTMLElement>()
const width = useWidth(box, 880)
const PAD = 36, R = 15
const toneText = { severe: '疑似串通', medium: '中等线索', weak: '弱线索' } as const

// 排序：按代号顺序走，遇到某组的第一家时把整组依次放下（组内仍按代号）
const order = computed(() => {
  const seen = new Set<string>(), out: ArcNode[] = []
  const byId = Object.fromEntries(props.nodes.map(n => [n.id, n]))
  for (const n of props.nodes) {
    if (seen.has(n.id)) continue
    const g = props.groups.find(x => x.ids.includes(n.id))
    for (const id of g ? props.nodes.map(x => x.id).filter(id => g.ids.includes(id)) : [n.id]) {
      if (!seen.has(id) && byId[id]) { seen.add(id); out.push(byId[id]!) }
    }
  }
  return out
})
const step = computed(() => order.value.length > 1 ? (width.value - PAD * 2) / (order.value.length - 1) : 0)
const x = computed(() => Object.fromEntries(order.value.map((n, i) => [n.id, order.value.length > 1 ? PAD + i * step.value : width.value / 2])))
const span = (e: ArcEdge) => Math.abs(x.value[e.a]! - x.value[e.b]!)
const lift = computed(() => Math.min(150, Math.max(56, ...props.edges.map(e => span(e) / 2 * 0.72))))
const base = computed(() => lift.value + 20)
const H = computed(() => base.value + R + 64)

const linked = computed(() => new Set(props.edges.flatMap(e => [e.a, e.b])))
const hover = ref<ArcEdge | null>(null), hoverNode = ref('')
const arcs = computed(() => [...props.edges]
  // 粗的、强的画在下层，细弧不被盖住
  .sort((p, q) => (q.tone === 'severe' ? 2 : q.tone === 'medium' ? 1 : 0) - (p.tone === 'severe' ? 2 : p.tone === 'medium' ? 1 : 0) || q.count - p.count)
  .map((e, i) => {
    const [x1, x2] = [x.value[e.a]!, x.value[e.b]!].sort((m, n) => m - n) as [number, number]
    const h = Math.min(lift.value, (x2 - x1) / 2 * 0.72), y = base.value - R - 2
    return { ...e, i, d: `M ${x1} ${y} C ${x1} ${y - h * 1.33}, ${x2} ${y - h * 1.33}, ${x2} ${y}`, mx: (x1 + x2) / 2, my: y - h,
      w: Math.min(9, 1.75 + (e.count - 1) * 1.25) }
  }))
// 说明框贴在弧顶上方；靠左右两端时往里收，不出卡片
const tip = computed(() => {
  const a = hover.value && arcs.value.find(x => x.a === hover.value!.a && x.b === hover.value!.b)
  return a ? { x: Math.min(Math.max(a.mx, 130), width.value - 130), y: a.my - 10 } : null
})
const lit = (e: ArcEdge) => {
  if (hover.value) return hover.value === e || (hover.value.a === e.a && hover.value.b === e.b)
  const n = hoverNode.value || props.focus
  if (n) return e.a === n || e.b === n
  return true
}
const nodeLit = (id: string) => {
  if (hover.value) return hover.value.a === id || hover.value.b === id
  const n = hoverNode.value || props.focus
  return !n || n === id || props.edges.some(e => (e.a === n && e.b === id) || (e.b === n && e.a === id))
}
const bands = computed(() => props.groups.filter(g => g.severe && g.ids.length >= 2).map(g => {
  const xs = g.ids.map(id => x.value[id]).filter((v): v is number => v !== undefined)
  return { key: g.ids.join(), x: Math.min(...xs) - R - 12, w: Math.max(...xs) - Math.min(...xs) + (R + 12) * 2, label: g.ids.length >= 3 ? '疑似围标' : '疑似串通' }
}))
const byId = computed(() => Object.fromEntries(props.nodes.map(n => [n.id, n])))
const who = (id: string) => `${byId.value[id]?.code ?? ''} ${byId.value[id]?.short ?? ''}`.trim()
const caption = computed(() => {
  const severe = props.groups.filter(g => g.severe)
  const parts = severe.map(g => `${g.ids.map(id => byId.value[id]?.code).join('、')} ${g.ids.length >= 3 ? '疑似围标' : '疑似串通'}`)
  const other = props.edges.filter(e => e.tone !== 'severe').length
  if (other) parts.push(`另有 ${other} 对之间有提示线索`)
  return parts.length ? parts.join('；') + '。' : '各家之间未见串标线索。'
})
function key(ev: KeyboardEvent, fn: () => void) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fn() } }
</script>

<template>
  <figure ref="box" class="chart arcs" @mouseleave="hover = null; hoverNode = ''">
    <svg :width="width" :height="H" role="group" :aria-label="`串标关系图：${caption}`">
      <defs>
        <filter id="arc-soft" x="-10%" y="-40%" width="120%" height="180%"><feGaussianBlur stdDeviation="3" /></filter>
      </defs>
      <!-- 疑似串通的一组：基线下的色带 -->
      <g v-for="b in bands" :key="b.key" class="band">
        <rect :x="b.x" :y="base - R - 8" :width="b.w" :height="R * 2 + 58" rx="16" />
        <text :x="b.x + b.w / 2" :y="base + R + 43" text-anchor="middle">{{ b.label }}</text>
      </g>
      <line class="baseline" :x1="PAD - 18" :x2="width - PAD + 18" :y1="base" :y2="base" />
      <!-- 弧 -->
      <g v-for="a in arcs" :key="a.a + a.b" class="hit arc" :class="[`t-${a.tone}`, { dim: !lit(a), on: active === a.entry }]"
        tabindex="0" role="button" :aria-label="`${byId[a.a]?.name} 与 ${byId[a.b]?.name}：${toneText[a.tone]}，${a.kinds.join('、')}`"
        @mouseenter="hover = a" @focus="hover = a" @blur="hover = null" @click="emit('open', a.entry)" @keydown="key($event, () => emit('open', a.entry))">
        <path v-if="a.tone === 'severe'" class="glow" :d="a.d" :stroke-width="a.w + 6" filter="url(#arc-soft)" />
        <path class="hitpath" :d="a.d" stroke-width="18" />
        <path class="stroke" :d="a.d" :stroke-width="a.w" pathLength="1" :style="{ animationDelay: `${120 + a.i * 90}ms` }" />
      </g>
      <!-- 节点 -->
      <g v-for="(n, i) in order" :key="n.id" class="hit node" :class="{ linked: linked.has(n.id), dim: !nodeLit(n.id), focus: focus === n.id }"
        :style="{ animationDelay: `${i * 40}ms` }" tabindex="0" role="button" :aria-label="`只看${n.name}`"
        @mouseenter="hoverNode = n.id" @mouseleave="hoverNode = ''" @click="emit('bidder', n.id)" @keydown="key($event, () => emit('bidder', n.id))">
        <title>{{ n.name }}</title>
        <circle class="ring" :cx="x[n.id]" :cy="base" :r="R" />
        <text class="code" :x="x[n.id]" :y="base + 4.5" text-anchor="middle">{{ n.code }}</text>
        <text class="name" :x="x[n.id]" :y="base + R + 18" text-anchor="middle">{{ n.short }}</text>
      </g>
    </svg>
    <!-- 悬停说明 -->
    <Transition name="tip">
      <div v-if="hover && tip" class="tip" :class="`t-${hover.tone}`" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }">
        <b>{{ who(hover.a) }} <span class="link">—</span> {{ who(hover.b) }}</b>
        <span class="tone"><i />{{ toneText[hover.tone] }} · {{ hover.count }} 条</span>
        <span class="kinds">{{ hover.kinds.join(' · ') }}</span>
        <span class="go">点击查看证据</span>
      </div>
    </Transition>
    <figcaption>
      <span>{{ caption }}</span>
      <span class="legend" aria-hidden="true">
        <span><i class="l-severe" />疑似串通</span><span><i class="l-medium" />中等线索</span><span><i class="l-weak" />弱线索</span><span class="muted">线越粗，线索越多</span>
      </span>
    </figcaption>
  </figure>
</template>

<style scoped>
.arcs { position: relative; gap: 4px; }
.arcs svg { width: 100%; }
.baseline { stroke: var(--si-hair); stroke-width: 1; }

.band rect { fill: var(--si-danger-soft); stroke: color-mix(in srgb, var(--si-danger) 22%, transparent); stroke-width: 1; stroke-dasharray: 4 4; }
.band text { font: var(--si-font-caption); fill: var(--si-danger-ink); font-weight: 600; letter-spacing: .04em; }

.arc .stroke, .arc .glow, .arc .hitpath { fill: none; stroke-linecap: round; }
.arc .hitpath { stroke: transparent; }
.arc .stroke { stroke-dasharray: 1; stroke-dashoffset: 1; animation: arc-draw var(--si-d4) var(--si-ease-out) forwards; transition: stroke-width var(--si-d2) var(--si-ease-out), opacity var(--si-d2); }
.arc .glow { stroke: var(--si-danger); opacity: .28; animation: arc-fade var(--si-d4) var(--si-ease-out) both; }
.t-severe .stroke { stroke: var(--si-danger); }
.t-medium .stroke { stroke: var(--si-warn); }
.t-weak .stroke { stroke: var(--si-ink-4); stroke-dasharray: none; stroke-dashoffset: 0; animation: arc-fade var(--si-d4) var(--si-ease-out) both; }
.arc { transition: opacity var(--si-d2) var(--si-ease-out); }
.arc.dim { opacity: .14; }
.arc:hover .stroke, .arc:focus-visible .stroke, .arc.on .stroke { filter: drop-shadow(0 0 4px color-mix(in srgb, currentColor 30%, transparent)); }
.arc:focus-visible .stroke { stroke: var(--si-focus); }

.node { transition: opacity var(--si-d2) var(--si-ease-out); animation: node-in var(--si-d3) var(--si-ease-out) both; transform-box: fill-box; transform-origin: center; }
.node .ring { fill: var(--si-surface-1); stroke: var(--si-ink-5); stroke-width: 1.5; transition: fill var(--si-d2), stroke var(--si-d2); }
.node .code { font: var(--si-font-label-sm); font-weight: 700; fill: var(--si-ink-3); }
.node .name { font: var(--si-font-caption); fill: var(--si-ink-3); }
.node.linked .ring { fill: var(--si-brand); stroke: var(--si-brand); }
.node.linked .code { fill: var(--si-surface-1); }
.node.linked .name { fill: var(--si-ink-1); font-weight: 600; }
.node:hover .ring, .node:focus-visible .ring { stroke: var(--si-focus); stroke-width: 3; }
.node.focus .ring { stroke: var(--si-ink-1); stroke-width: 3; }
.node.dim { opacity: .3; }

.tip { position: absolute; transform: translate(-50%, -100%); display: flex; flex-direction: column; gap: 3px; min-width: 180px; max-width: 300px; padding: 10px 14px;
  border-radius: var(--si-radius-md); background: var(--si-surface-pop); box-shadow: var(--si-shadow-3); pointer-events: none; z-index: 2; }
.tip b { font: var(--si-font-label-sm); color: var(--si-ink-1); white-space: nowrap; }
.tip .link { color: var(--si-ink-4); font-weight: 400; margin: 0 2px; }
.tip .tone { display: flex; align-items: center; gap: 6px; font: var(--si-font-caption); font-weight: 600; }
.tip .tone i { width: 8px; height: 8px; border-radius: 50%; }
.tip.t-severe .tone { color: var(--si-danger-ink); } .tip.t-severe .tone i { background: var(--si-danger); }
.tip.t-medium .tone { color: var(--si-warn-ink); } .tip.t-medium .tone i { background: var(--si-warn); }
.tip.t-weak .tone { color: var(--si-ink-3); } .tip.t-weak .tone i { background: var(--si-ink-4); }
.tip .kinds { font: var(--si-font-caption); color: var(--si-ink-2); }
.tip .go { font: var(--si-font-caption); color: var(--si-ink-4); }
.tip-enter-active, .tip-leave-active { transition: opacity var(--si-d2) var(--si-ease-out), transform var(--si-d2) var(--si-ease-out); }
.tip-enter-from, .tip-leave-to { opacity: 0; transform: translate(-50%, calc(-100% + 6px)); }

figcaption { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font: var(--si-font-caption); color: var(--si-ink-3); }
.legend i { display: inline-block; width: 16px; height: 3px; margin-right: 6px; border-radius: 2px; vertical-align: middle; }
.legend .l-severe { background: var(--si-danger); height: 4px; }
.legend .l-medium { background: var(--si-warn); }
.legend .l-weak { background: var(--si-ink-4); }
.legend .muted { color: var(--si-ink-4); }

@keyframes arc-draw { to { stroke-dashoffset: 0; } }
@keyframes arc-fade { from { opacity: 0; } }
@keyframes node-in { from { opacity: 0; transform: scale(.6); } }
@media (prefers-reduced-motion: reduce) {
  .arc .stroke, .arc .glow, .node { animation: none; stroke-dashoffset: 0; }
}
</style>
