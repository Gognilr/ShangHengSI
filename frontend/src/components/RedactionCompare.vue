<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode } from '@/api/preview'
import { redactionApi, REDACTION_ERROR_MESSAGES, type RedactionInfo, type RedactionItemPos, type Suspect, type UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import { buildCompareRows, buildNoteRow, type CompareRow, type CompareSeg } from '@/utils/compare'
import { REDACTION_KIND, MANUAL_CATEGORY_OPTIONS, toOptions } from '@/utils/labels'

const props = withDefaults(defineProps<{ taskId: string; files: TaskFile[]; outboundText: string; note?: string | null; outboundNote?: string | null; items: RedactionItemPos[]; suspects: Suspect[]; loading?: boolean; editable?: boolean }>(), { loading: false, editable: true })
const emit = defineEmits<{ done: [info: RedactionInfo, action: 'add' | 'revoke']; stale: []; busy: [value: boolean] }>()
const nodes = ref<PreviewNode[]>([])
const nodeErrors = ref<Record<number, string>>({})
const loadingNodes = ref(false)
const activeFile = ref(0)
const changedOnly = ref(true)
const expanded = ref(new Set<number>())
const grid = ref<HTMLElement>()
const hovered = ref<{ loc: string | null; kind: CompareRow['kind']; placeholder: string } | null>(null)
const focusedLoc = ref('')
const flashLoc = ref('')
let version = 0
let flashTimer: ReturnType<typeof setTimeout> | undefined

async function loadNodes() {
  const current = ++version
  loadingNodes.value = true
  nodes.value = []
  nodeErrors.value = {}
  const results = await Promise.all(props.files.map(async file => {
    try {
      const result = await previewApi.nodes(props.taskId, file.id)
      return { file, nodes: result.items, error: result.text_available ? '' : '原文已按保留期清理' }
    } catch (e) {
      return { file, nodes: [], error: e instanceof ApiError ? e.message : '加载失败，请刷新页面' }
    }
  }))
  if (current !== version) return
  nodes.value = results.flatMap(result => result.nodes)
  nodeErrors.value = Object.fromEntries(results.filter(result => result.error).map(result => [result.file.seq, result.error]))
  loadingNodes.value = false
}
watch(() => [props.taskId, props.files.map(file => file.id).join(',')], loadNodes, { immediate: true })
const rows = computed(() => {
  const body = buildCompareRows(props.outboundText, nodes.value, props.items, props.suspects)
  const note = buildNoteRow(props.note ?? '', props.outboundNote ?? null, props.items, props.suspects)
  return note ? [...body, note] : body
})
const fileSeq = (loc: string | null) => Number(/^F(\d+)(?:-|$)/.exec(loc ?? '')?.[1] ?? 0)
const fileRows = computed(() => rows.value.filter(row => !activeFile.value || row.kind === 'header' || row.loc === 'NOTE' || fileSeq(row.loc) === activeFile.value))
const changedCount = computed(() => fileRows.value.filter(row => row.kind !== 'header' && row.hasRedaction).length)
const unmatchedCount = computed(() => fileRows.value.filter(row => row.kind === 'unmatched').length)
const bodyCount = computed(() => fileRows.value.filter(row => row.kind !== 'header').length)
function countForFile(seq: number) { return rows.value.filter(row => fileSeq(row.loc) === seq && row.hasRedaction).length }

type DisplayEntry = { row: CompareRow; index: number } | { hidden: number; index: number }
const displayRows = computed(() => {
  const result: DisplayEntry[] = []
  let hidden: { hidden: number; index: number } | null = null
  const flush = () => { if (hidden) result.push(hidden); hidden = null }
  fileRows.value.forEach((row, index) => {
    const show = !changedOnly.value || row.hasRedaction || row.hasPendingSuspect || row.kind === 'header' || row.kind === 'table_note' || row.kind === 'unmatched' || row.loc === focusedLoc.value
    if (show) { flush(); result.push({ row, index }); return }
    if (!hidden) hidden = { hidden: 0, index }
    hidden.hidden++
  })
  flush()
  return result.flatMap(entry => 'hidden' in entry && expanded.value.has(entry.index)
    ? fileRows.value.slice(entry.index, entry.index + entry.hidden).map((row, offset) => ({ row, index: entry.index + offset })) : [entry])
})
watch([activeFile, changedOnly, rows], () => { expanded.value = new Set(); clearSelection() })
const pairedLocs = computed(() => new Set(rows.value.filter(row => row.kind === 'pair').map(row => row.loc)))
function originalLoc(row: CompareRow, index: number): string {
  if (row.kind !== 'grouped') return row.loc ?? ''
  return nodes.value.filter(node => node.loc.startsWith(`${row.loc}-`) && !pairedLocs.value.has(node.loc))[index]?.loc ?? ''
}
const kind = (seg: CompareSeg) => seg.suspect ? '' : `kind-${REDACTION_KIND[seg.category ?? ''] ?? 'other'}`
const linked = (row: CompareRow, seg: CompareSeg) => !!seg.placeholder && hovered.value?.loc === row.loc && hovered.value?.kind === row.kind && hovered.value.placeholder === seg.placeholder
const legend = [{ kind: 'org', label: '公司、银行' }, { kind: 'person', label: '人名' }, { kind: 'contact', label: '联系方式' }, { kind: 'id', label: '账号、编号' }, { kind: 'addr', label: '地址、邮编' }, { kind: 'amount', label: '金额、单价、数量' }, { kind: 'other', label: '敏感词、项目' }]

async function locate(loc: string) {
  focusedLoc.value = loc
  activeFile.value = fileSeq(loc)
  await nextTick()
  if (!grid.value) return
  // 对照区不完整可见时（顶栏高 64px），先把整个网格滚到视口中部，用户才能看到定位结果。
  const box = grid.value.getBoundingClientRect()
  if (box.top < 64 || box.bottom > innerHeight) grid.value.scrollIntoView({ block: 'center' })
  const target = Array.from(grid.value.querySelectorAll<HTMLElement>('.compare-row')).find(element => element.dataset.loc === loc || loc.startsWith(`${element.dataset.loc}-`))
  if (!target) return
  // 行定位只改网格自身的 scrollTop，不再带动外层页面。
  grid.value.scrollTop += target.getBoundingClientRect().top - grid.value.getBoundingClientRect().top - 52
  flashLoc.value = target.dataset.loc ?? ''
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashLoc.value = '' }, 1800)
}
defineExpose({ locate })

interface Selection { loc: string; start: number; end: number; text: string; left: number; top: number }
const selection = ref<Selection | null>(null)
const selectionNotice = ref('')
const saveError = ref('')
const saving = ref(false)
const category = ref<UserCategory>('ORG')
const categoryOptions = toOptions(MANUAL_CATEGORY_OPTIONS)

function originalItem(row: CompareRow, segments: CompareSeg[], index: number, line: number) {
  const loc = originalLoc(row, line)
  const start = segments.slice(0, index).reduce((n, seg) => n + Array.from(seg.text).length, 0)
  return props.items.find(item => item.loc === loc && item.char_start === start &&
    item.char_end === start + Array.from(segments[index]?.text ?? '').length && item.can_revoke)
}
function outboundItem(row: CompareRow, segments: CompareSeg[], index: number) {
  if (row.kind !== 'pair' || !row.loc) return undefined
  const placeholder = segments[index]?.placeholder
  if (!placeholder) return undefined
  const occurrence = segments.slice(0, index).filter(seg => seg.placeholder === placeholder).length
  const item = props.items.filter(item => item.loc === row.loc && item.placeholder === placeholder)
    .sort((a, b) => a.char_start - b.char_start)[occurrence]
  return item?.can_revoke ? item : undefined
}
async function revokeItem(item: RedactionItemPos | undefined) {
  if (!item || !props.editable || saving.value) return
  try {
    await ElMessageBox.confirm('撤销后，同一文字将在全文恢复原样。确认它不是敏感信息后再撤销。',
      '撤销误识别', { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' })
  } catch { return }
  saving.value = true
  emit('busy', true)
  saveError.value = ''
  try {
    const info = await redactionApi.revoke(props.taskId, { loc: item.loc, char_start: item.char_start, char_end: item.char_end })
    clearSelection()
    emit('done', info, 'revoke')
  } catch (e) {
    saveError.value = e instanceof ApiError ? REDACTION_ERROR_MESSAGES[e.code] ?? e.message : '撤销失败，请重试'
    if (e instanceof ApiError && e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } finally { saving.value = false; emit('busy', false) }
}
function clearSelection() { selection.value = null; selectionNotice.value = ''; saveError.value = '' }
function onWindowResize() {
  if (!selection.value) return
  selection.value.left = Math.max(8, Math.min(selection.value.left, innerWidth - 420))
  selection.value.top = Math.max(8, Math.min(selection.value.top, innerHeight - 360))
}
function onPageScroll(event: Event) {
  const target = event.target
  if (target === document || target === window || target === grid.value
      || (target instanceof Element && grid.value && target.contains(grid.value))) {
    window.getSelection()?.removeAllRanges()
    clearSelection()
  }
}
function onMouseUp() {
  if (!props.editable || saving.value) return
  const selected = window.getSelection()
  if (!selected || !selected.rangeCount || selected.isCollapsed) return
  const range = selected.getRangeAt(0)
  const holder = (node: Node) => (node.nodeType === 1 ? node as Element : node.parentElement)?.closest<HTMLElement>('.compare-original')
  const startElement = holder(range.startContainer)
  const endElement = holder(range.endContainer)
  clearSelection()
  if (!startElement || startElement !== endElement || !grid.value?.contains(startElement)) return
  const pre = document.createRange()
  pre.selectNodeContents(startElement)
  pre.setEnd(range.startContainer, range.startOffset)
  const full = startElement.textContent ?? ''
  const start = Array.from(full.slice(0, pre.toString().length)).length
  const text = range.toString()
  const end = start + Array.from(text).length
  const loc = startElement.dataset.loc ?? ''
  if (!loc) return
  if (props.items.some(item => item.loc === loc && item.char_start < end && item.char_end > start)) { selectionNotice.value = '所选文字已被替换'; return }
  if (Array.from(text.trim()).length < 2 || Array.from(text.trim()).length > 100) { selectionNotice.value = '请选择 2–100 个字符'; return }
  const bounds = range.getBoundingClientRect()
  selection.value = { loc, start, end, text, left: Math.max(8, Math.min(bounds.left, innerWidth - 420)), top: Math.max(8, Math.min(bounds.bottom + 8, innerHeight - 360)) }
}
async function saveSelection() {
  if (!selection.value || saving.value) return
  saving.value = true
  emit('busy', true)
  saveError.value = ''
  try {
    const info = await redactionApi.manual(props.taskId, { loc: selection.value.loc, char_start: selection.value.start, char_end: selection.value.end, category: category.value })
    window.getSelection()?.removeAllRanges()
    clearSelection()
    emit('done', info, 'add')
  } catch (e) {
    saveError.value = e instanceof ApiError ? REDACTION_ERROR_MESSAGES[e.code] ?? e.message : '保存失败，请重试'
    if (e instanceof ApiError && e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } finally { saving.value = false; emit('busy', false) }
}
onMounted(() => {
  window.addEventListener('resize', onWindowResize)
  window.addEventListener('scroll', onPageScroll, true)
})
onBeforeUnmount(() => {
  version++; clearTimeout(flashTimer)
  window.removeEventListener('resize', onWindowResize)
  window.removeEventListener('scroll', onPageScroll, true)
})
</script>

<template>
  <div v-loading="loading || loadingNodes" class="compare">
    <p class="hint">左侧原文仅在本机显示；右侧是确认后实际发送给模型的文本</p>
    <div class="compare-toolbar">
      <el-radio-group v-model="activeFile" size="small" aria-label="对照文件" class="file-options">
        <el-radio-button :value="0">全部文件</el-radio-button>
        <el-radio-button v-for="file in files" :key="file.id" :value="file.seq">{{ file.original_name }} · {{ countForFile(file.seq) }} 段有替换</el-radio-button>
      </el-radio-group>
      <el-checkbox v-model="changedOnly">只看有替换的段落</el-checkbox>
      <span class="hint">共 {{ bodyCount }} 段，{{ changedCount }} 段有替换 <span v-if="unmatchedCount" class="warning">{{ unmatchedCount }} 段未能对齐</span></span>
    </div>
    <div class="legend">
      <span v-for="entry in legend" :key="entry.kind"><i class="swatch" :class="`kind-${entry.kind}`" />{{ entry.label }}</span>
      <span><i class="swatch suspect" />疑似遗漏（未处理，将原样发送）</span>
    </div>
    <p class="hint">悬停任一处，两侧对应内容同时高亮<span v-if="editable">；在左侧选中文字可直接补充脱敏</span></p>
    <el-alert v-for="(message, seq) in nodeErrors" :key="seq" type="warning" :closable="false" show-icon :title="`原文读取失败：${files.find(file => file.seq === Number(seq))?.original_name ?? ''}`" :description="message" class="gap" />
    <div ref="grid" class="compare-grid" @mouseup="onMouseUp">
      <div class="compare-head"><span>位置</span><span>原文（仅本机）</span><span>将发送的文本</span></div>
      <template v-for="entry in displayRows" :key="entry.index">
        <div v-if="'hidden' in entry" class="fold-row"><el-button link type="primary" @click="expanded.add(entry.index)">已隐藏 {{ entry.hidden }} 段无替换的段落 · 展开</el-button></div>
        <div v-else class="compare-row" :class="{ 'has-suspect': entry.row.hasPendingSuspect, 'is-flashing': flashLoc === entry.row.loc }" :data-loc="entry.row.loc">
          <div class="location">{{ entry.row.label }}<span v-if="entry.row.hasPendingSuspect" class="pending-label">待处理</span></div>
          <div class="original-cell">
            <template v-if="!nodeErrors[fileSeq(entry.row.loc)]">
              <p v-for="(segments, index) in entry.row.left" :key="index" class="compare-original" :data-loc="entry.row.kind === 'header' ? undefined : originalLoc(entry.row, index)"><template v-for="(seg, i) in segments" :key="i"><el-popover v-if="seg.placeholder || seg.suspect" trigger="hover" :width="240" :disabled="!editable || saving || !originalItem(entry.row, segments, i, index)"><template #reference><span class="segment" :class="[kind(seg), { replaced: seg.placeholder, suspect: seg.suspect, linked: linked(entry.row, seg) }]" :title="seg.suspect ? '待处理' : seg.placeholder" :tabindex="seg.placeholder ? 0 : undefined" @mouseenter="hovered = seg.placeholder ? { loc: entry.row.loc, kind: entry.row.kind, placeholder: seg.placeholder } : null" @mouseleave="hovered = null" @focus="hovered = seg.placeholder ? { loc: entry.row.loc, kind: entry.row.kind, placeholder: seg.placeholder } : null" @blur="hovered = null">{{ seg.text }}</span></template><el-button link type="primary" :loading="saving" @click="revokeItem(originalItem(entry.row, segments, i, index))">撤销（不是敏感信息）</el-button></el-popover><template v-else>{{ seg.text }}</template></template></p>
              <span v-if="!entry.row.left.length" class="hint">—</span>
            </template>
            <span v-else class="hint">原文读取失败</span>
          </div>
          <div class="outbound-cell">
            <div v-if="entry.row.kind === 'grouped'" class="hint">报价明细已汇总发送</div>
            <p v-if="entry.row.right !== null" class="outbound-text"><template v-for="(seg, i) in entry.row.right" :key="i"><el-popover v-if="seg.placeholder || seg.suspect" trigger="hover" :width="240" :disabled="!editable || saving || !outboundItem(entry.row, entry.row.right, i)"><template #reference><span class="segment" :class="[kind(seg), { placeholder: seg.placeholder, suspect: seg.suspect, linked: linked(entry.row, seg) }]" :title="seg.suspect ? '待处理' : undefined" :tabindex="seg.placeholder ? 0 : undefined" @mouseenter="hovered = seg.placeholder ? { loc: entry.row.loc, kind: entry.row.kind, placeholder: seg.placeholder } : null" @mouseleave="hovered = null" @focus="hovered = seg.placeholder ? { loc: entry.row.loc, kind: entry.row.kind, placeholder: seg.placeholder } : null" @blur="hovered = null">{{ seg.text }}</span></template><el-button link type="primary" :loading="saving" @click="revokeItem(outboundItem(entry.row, entry.row.right, i))">撤销（不是敏感信息）</el-button></el-popover><template v-else>{{ seg.text }}</template></template></p>
            <span v-else class="hint">—（不发送）</span>
          </div>
        </div>
      </template>
      <el-empty v-if="!displayRows.length && !loading && !loadingNodes" description="没有可对照的段落" :image-size="48" />
    </div>
    <el-alert v-if="selectionNotice" type="warning" :closable="false" show-icon :title="selectionNotice" class="gap" />
    <el-alert v-if="saveError && !selection" type="error" :closable="false" show-icon :title="saveError" class="gap" />
    <div v-if="selection" class="selection-popover" :style="{ left: `${selection.left}px`, top: `${selection.top}px` }" role="dialog" aria-label="设为敏感信息">
      <p class="picked">已选：{{ selection.text }}</p>
      <el-radio-group v-model="category" :disabled="saving" aria-label="替换类别"><el-radio v-for="option in categoryOptions" :key="option.value" :value="option.value">{{ option.label }}</el-radio></el-radio-group>
      <div class="selection-actions"><el-button size="small" :disabled="saving" @click="clearSelection">取消</el-button><el-button size="small" type="primary" :loading="saving" @click="saveSelection">设为敏感信息</el-button></div>
      <el-alert v-if="saveError" type="error" show-icon :closable="false" :title="saveError" class="gap" />
    </div>
  </div>
</template>

<style scoped>
.compare-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.file-options { display: flex; flex-wrap: wrap; gap: 4px; max-width: 100%; }
.file-options :deep(.el-radio-button__inner) { max-width: 340px; overflow: hidden; text-overflow: ellipsis; border-radius: 6px; }
.legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px; color: var(--el-text-color-secondary); }
.legend > span { display: inline-flex; align-items: center; gap: 4px; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 4px; background: var(--kind-bg); border-bottom: 2px solid var(--kind-color); }
.compare-grid { position: relative; max-height: 560px; overflow: auto; border: 1px solid var(--el-border-color-light); border-radius: 12px; margin-top: 16px; }
.compare-head, .compare-row { display: grid; grid-template-columns: 112px minmax(0, 1fr) minmax(0, 1fr); }
.compare-head { position: sticky; top: 0; z-index: 2; background: var(--el-fill-color-light); font-size: 13px; font-weight: 600; }
.compare-head > span, .compare-row > div { padding: 12px; min-width: 0; }
.compare-row { border-top: 1px solid var(--el-border-color-lighter); border-left: 3px solid transparent; }
.compare-row > div + div { border-left: 1px solid var(--el-border-color-lighter); }
.has-suspect { border-left-color: var(--el-color-warning); }
.is-flashing { background: var(--el-color-warning-light-9); }
.location { font-size: 12px; color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.pending-label { display: block; margin-top: 8px; color: var(--el-color-warning); }
.compare-original, .outbound-text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.9; font-size: 14px; }
.compare-original + .compare-original { margin-top: 8px; }
.replaced { background: var(--kind-bg); border-bottom: 2px solid var(--kind-color); }
.placeholder { font-family: ui-monospace, Consolas, monospace; background: var(--kind-bg); color: var(--kind-color); border-radius: 6px; padding: 0 4px; }
.linked { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
.suspect { --kind-bg: var(--el-color-warning-light-9); --kind-color: var(--el-color-warning); background: var(--kind-bg); border-bottom: 2px solid var(--kind-color); }
.kind-org { --kind-bg: var(--si-k-org-bg); --kind-color: var(--si-k-org); }
.kind-person { --kind-bg: var(--si-k-person-bg); --kind-color: var(--si-k-person); }
.kind-contact { --kind-bg: var(--si-k-contact-bg); --kind-color: var(--si-k-contact); }
.kind-id { --kind-bg: var(--si-k-id-bg); --kind-color: var(--si-k-id); }
.kind-addr { --kind-bg: var(--si-k-addr-bg); --kind-color: var(--si-k-addr); }
.kind-other { --kind-bg: var(--si-k-other-bg); --kind-color: var(--si-k-other); }
.kind-amount { --kind-bg: var(--si-k-amount-bg); --kind-color: var(--si-k-amount); }
.fold-row { padding: 8px 12px; text-align: center; background: var(--el-fill-color-lighter); border-top: 1px solid var(--el-border-color-lighter); }
.warning { color: var(--el-color-warning); margin-left: 8px; }
.selection-popover { position: fixed; z-index: 3000; width: 380px; max-height: calc(100vh - 16px); overflow: auto; padding: 16px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: 12px; box-shadow: var(--el-box-shadow-light); }
.picked { margin: 0 0 8px; max-height: 64px; overflow: auto; overflow-wrap: anywhere; font-size: 13px; }
.selection-actions { display: flex; justify-content: flex-end; margin-top: 12px; gap: 8px; }
</style>
