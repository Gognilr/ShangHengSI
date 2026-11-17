<script setup lang="ts">
import { computed, ref } from 'vue'
import { levelClass, useWidth } from './chart'

// 串标线索关系图（V34Q-27 第三部分第 3 条）：投标人为节点、线索为连线；线粗按条数，颜色按最高等级（强 / 中 / 弱）。
// 点连线只看这一对，点节点只看这一家；连线与节点都可用 Tab 聚焦、回车选择。
export interface GraphNode { id: string; label: string; name: string }
export interface GraphEdge { a: string; b: string; counts: { level: string; n: number }[] }
const props = defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; selected?: string[] | null }>()
const emit = defineEmits<{ pick: [ids: string[]] }>()
const box = ref<HTMLElement>(), width = useWidth(box)
const H = 248
const text: Record<string, string> = { strong: '强', medium: '中', weak: '弱' }
const center = computed(() => ({ x: Math.min(width.value, 640) / 2, y: H / 2 }))
const pos = computed(() => {
  const n = props.nodes.length, rx = Math.min(width.value, 640) / 2 - 90, ry = H / 2 - 40
  return Object.fromEntries(props.nodes.map((node, i) => {
    const t = -Math.PI / 2 + (2 * Math.PI * i) / n
    return [node.id, { x: center.value.x + rx * Math.cos(t), y: center.value.y + ry * Math.sin(t), cos: Math.cos(t), sin: Math.sin(t) }]
  }))
})
const lines = computed(() => props.edges.filter(e => e.counts.length && pos.value[e.a] && pos.value[e.b]).map(e => {
  const p = pos.value[e.a]!, q = pos.value[e.b]!, total = e.counts.reduce((s, c) => s + c.n, 0)
  const top = ['strong', 'medium', 'weak'].find(l => e.counts.some(c => c.level === l)) ?? 'weak'
  return { ...e, p, q, total, top, w: Math.min(9, 1.5 + total * 1.2), label: e.counts.map(c => `${text[c.level]}${c.n}`).join(' '),
    on: !props.selected?.length || (props.selected.length === 2 ? props.selected.includes(e.a) && props.selected.includes(e.b) : props.selected.some(id => id === e.a || id === e.b)) }
}))
const names = computed(() => Object.fromEntries(props.nodes.map(n => [n.id, n])))
const linked = computed(() => new Set(lines.value.flatMap(l => [l.a, l.b])))
const caption = computed(() => {
  if (!lines.value.length) return '各家之间未见串标线索。'
  const top = [...lines.value].sort((x, y) => y.total - x.total)[0]!
  return `${lines.value.length} 对投标人之间有线索，最多的是 ${names.value[top.a]?.label} · ${names.value[top.b]?.label}（${top.total} 条）；点连线只看这一对，点名称只看这一家。`
})
function key(e: KeyboardEvent, ids: string[]) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); emit('pick', ids) } }
</script>

<template>
  <figure ref="box" class="chart graph">
    <svg :width="Math.min(width, 640)" :height="H" role="group" :aria-label="caption">
      <g v-for="l in lines" :key="l.a + l.b" class="hit" :class="levelClass(l.top)" :opacity="l.on ? 1 : 0.25" tabindex="0" role="button"
        :aria-label="`${names[l.a]?.name} 与 ${names[l.b]?.name}：${l.label.replace(/ /g, '、')}`" @click="emit('pick', [l.a, l.b])" @keydown="key($event, [l.a, l.b])">
        <title>{{ names[l.a]?.name }} · {{ names[l.b]?.name }}：{{ l.label }}</title>
        <line :x1="l.p.x" :y1="l.p.y" :x2="l.q.x" :y2="l.q.y" stroke="transparent" stroke-width="16" />
        <line class="ring" :x1="l.p.x" :y1="l.p.y" :x2="l.q.x" :y2="l.q.y" :stroke-width="l.w" stroke-linecap="round" fill="none" />
        <text class="halo" :x="(l.p.x + l.q.x) / 2" :y="(l.p.y + l.q.y) / 2 + 4" text-anchor="middle" style="fill: currentColor; stroke-width: 5px" :style="{ color: `var(--si-${l.top === 'strong' ? 'danger' : l.top === 'medium' ? 'warn' : 'ink-3'}${l.top === 'weak' ? '' : '-ink'})` }">{{ l.label }}</text>
      </g>
      <g v-for="node in nodes" :key="node.id" class="hit" tabindex="0" role="button" :aria-label="`只看${node.name}`" @click="emit('pick', [node.id])" @keydown="key($event, [node.id])">
        <title>{{ node.name }}</title>
        <circle class="ring" :cx="pos[node.id]!.x" :cy="pos[node.id]!.y" r="7" :style="linked.has(node.id) ? 'fill: var(--si-brand); stroke: var(--si-brand)' : 'fill: var(--si-bg-canvas); stroke: var(--si-ink-4)'" stroke-width="1.5" />
        <text class="halo" :class="{ strong: selected?.includes(node.id) }" :x="pos[node.id]!.x + pos[node.id]!.cos * 16" :y="pos[node.id]!.y + pos[node.id]!.sin * 18 + 4"
          :text-anchor="Math.abs(pos[node.id]!.cos) < 0.3 ? 'middle' : pos[node.id]!.cos > 0 ? 'start' : 'end'" style="font: var(--si-font-label-sm)">{{ node.label }}</text>
      </g>
    </svg>
    <figcaption>{{ caption }}</figcaption>
    <div class="legend" aria-hidden="true"><span><i style="background: var(--si-danger-ink)" />强</span><span><i style="background: var(--si-warn-ink)" />中</span><span><i style="background: var(--si-ink-4)" />弱</span><span>线越粗，线索越多</span></div>
  </figure>
</template>

<style scoped>
.graph text.halo { fill: var(--si-ink-2); }
</style>
