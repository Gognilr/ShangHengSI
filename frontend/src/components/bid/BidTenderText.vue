<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Plus } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidLoc, type BidTender } from '@/api/bid'

/**
 * 招标文件文字画布：原文视图高亮 ★ 句（赭黄）与否决句（红），清单已覆盖的范围加下划线；
 * 划选一段原文可“加入要求清单”。AI 视野只显示脱敏后的文字，不可划选。
 */
const props = defineProps<{ projectId: string; view: 'original' | 'ai'; reloadKey: string; focus: BidLoc | null; covered: BidLoc[] }>()
const emit = defineEmits<{ add: [loc: BidLoc, text: string] }>()

const data = ref<BidTender>(), error = ref(''), loading = ref(false)
const body = ref<HTMLElement>(), picked = ref<{ loc: BidLoc; text: string; top: number } | null>(null)

async function load() {
  loading.value = true; error.value = ''
  try { data.value = await bidApi.tender(props.projectId, props.view) }
  catch (e) { error.value = e instanceof ApiError ? e.message : '招标文件读取失败' }
  finally { loading.value = false }
}
watch(() => [props.projectId, props.view, props.reloadKey], () => { picked.value = null; void load() }, { immediate: true })

interface Seg { text: string; cls: string }
const marksByLoc = computed(() => {
  const map = new Map<string, { start: number; end: number; cls: string }[]>()
  const push = (loc: string, start: number, end: number, cls: string) => { if (!map.has(loc)) map.set(loc, []); map.get(loc)!.push({ start, end, cls }) }
  for (const m of data.value?.markers ?? []) push(m.loc, m.start, m.end, m.kind)
  for (const c of props.covered) push(c.loc, c.start, c.end, 'covered')
  if (props.focus) push(props.focus.loc, props.focus.start, props.focus.end, 'focus')
  return map
})
// 按边界切分，每段带上覆盖它的全部类别
function segments(loc: string, text: string): Seg[] {
  const marks = marksByLoc.value.get(loc)
  if (!marks?.length || props.view === 'ai') return [{ text, cls: '' }]
  const cuts = new Set([0, text.length])
  for (const m of marks) { cuts.add(Math.max(0, Math.min(text.length, m.start))); cuts.add(Math.max(0, Math.min(text.length, m.end))) }
  const points = [...cuts].sort((a, b) => a - b), out: Seg[] = []
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]!, b = points[i + 1]!
    if (a === b) continue
    const cls = [...new Set(marks.filter(m => m.start <= a && m.end >= b).map(m => m.cls))].join(' ')
    out.push({ text: text.slice(a, b), cls })
  }
  return out
}
// 表格单元格按行横排（位置如 F1-T1-R2-C3）；AI 视野不显示文件名（文件名不发送）
type TNode = BidTender['nodes'][number]
const rowOf = (n: TNode) => n.kind === 'table_cell' ? /^(.*R\d+)-?C\d+$/.exec(n.loc)?.[1] ?? null : null
const groups = computed(() => {
  const out: { fileId: string; name: string; blocks: { key: string; row: boolean; nodes: TNode[] }[] }[] = []
  for (const n of data.value?.nodes ?? []) {
    if (out[out.length - 1]?.fileId !== n.file_id) {
      const index = Object.keys(data.value!.files).indexOf(n.file_id) + 1
      out.push({ fileId: n.file_id, name: props.view === 'ai' ? `文件 ${index}` : data.value!.files[n.file_id] ?? '', blocks: [] })
    }
    const blocks = out[out.length - 1]!.blocks, row = rowOf(n), last = blocks[blocks.length - 1]
    if (row && last?.row && last.key === row) last.nodes.push(n)
    else blocks.push({ key: row ?? n.loc, row: !!row, nodes: [n] })
  }
  return out
})

watch(() => props.focus, async loc => {
  if (!loc) return
  await nextTick()
  body.value?.querySelector<HTMLElement>(`[data-loc="${CSS.escape(loc.loc)}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
})

function onSelect() {
  picked.value = null
  if (props.view !== 'original') return
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !sel.rangeCount) return
  const range = sel.getRangeAt(0)
  const el = (n: Node | null) => (n instanceof Element ? n : n?.parentElement)?.closest<HTMLElement>('[data-loc]')
  const para = el(range.startContainer)
  if (!para || para !== el(range.endContainer)) return  // 只支持同一段内划选
  const before = document.createRange()
  before.selectNodeContents(para); before.setEnd(range.startContainer, range.startOffset)
  const start = before.toString().length, text = range.toString()
  if (!text.trim()) return
  const box = body.value!.getBoundingClientRect(), rect = range.getBoundingClientRect()
  picked.value = { loc: { file_id: para.dataset.file!, loc: para.dataset.loc!, start, end: start + text.length }, text: text.trim(), top: rect.bottom - box.top + body.value!.scrollTop + 6 }
}
function add() { if (picked.value) { emit('add', picked.value.loc, picked.value.text); picked.value = null; window.getSelection()?.removeAllRanges() } }
</script>

<template>
  <div ref="body" class="canvas" :class="{ ai: view === 'ai' }" @mouseup="onSelect">
    <p v-if="loading && !data" class="hint" role="status">正在读取招标文件…</p>
    <p v-else-if="error" class="hint" role="alert">{{ error }}</p>
    <p v-else-if="!data?.nodes.length" class="hint">{{ view === 'ai' ? '招标文件尚未在本机脱敏' : '招标文件解析后在这里显示原文' }}</p>
    <template v-for="g in groups" :key="g.fileId">
      <h3 class="file">{{ g.name }}</h3>
      <div v-for="b in g.blocks" :key="b.key" :class="b.row ? 'row' : 'block'">
        <p v-for="n in b.nodes" :key="n.loc" :data-loc="n.loc" :data-file="n.file_id" :class="['node', n.kind]"><template v-for="(s, i) in segments(n.loc, n.text)" :key="i"><span v-if="s.cls" :class="s.cls">{{ s.text }}</span><template v-else>{{ s.text }}</template></template></p>
      </div>
    </template>
    <el-button v-if="picked" class="pick" size="small" type="primary" :style="{ top: `${picked.top}px` }" @mousedown.prevent @click="add"><Plus :size="14" :stroke-width="2" />加入要求清单</el-button>
  </div>
</template>

<style scoped>
.canvas { position: relative; height: 100%; overflow: auto; padding: 4px 20px 24px 0; font: var(--si-font-body); color: var(--si-text-reading); }
.file { position: sticky; top: 0; z-index: 1; margin: 0 0 8px; padding: 8px 0; background: var(--si-bg-canvas); font: var(--si-font-label); color: var(--si-ink-3); border-bottom: 1px solid var(--si-hair); }
.node { margin: 0 0 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
.node.heading { margin-top: 18px; font-weight: 600; color: var(--si-ink-1); }
.row { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); border-bottom: 1px solid var(--si-hair-faint); }
.row:first-child, .block + .row { border-top: 1px solid var(--si-hair); }
.row .node { margin: 0; padding: 6px 8px; font: var(--si-font-meta); color: var(--si-ink-2); }
.row + .block { margin-top: 10px; }
.star { background: var(--si-hl-medium); border-radius: 2px; }
.veto { background: var(--si-hl-high); border-radius: 2px; }
.covered { text-decoration: underline 1px var(--si-ink-4); text-underline-offset: 4px; }
.focus { outline: 2px solid var(--si-brand); outline-offset: 1px; border-radius: 2px; }
.ai { color: var(--si-ink-2); }
.pick { position: absolute; left: 0; z-index: 2; }
.pick svg { margin-right: 4px; }
</style>
