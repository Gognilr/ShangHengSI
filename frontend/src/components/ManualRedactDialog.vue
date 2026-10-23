<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { previewApi, type PreviewNode } from '@/api/preview'
import { REDACTION_ERROR_MESSAGES, redactionApi, type RedactionItemPos, type RedactionInfo, type UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import { USER_CATEGORY_OPTIONS, REDACTION_KIND, toOptions } from '@/utils/labels'
import { splitOriginal, type CompareSeg } from '@/utils/compare'

/**
 * 手动补充（设计 M4 第六节）：按节点原文显示，用户在同一个节点内选中文字后设为敏感信息。
 * 每个节点的文字放在独立的 .txt 元素中，元素文字内容与原文逐字相同（片段只是拆成多个 span）；
 * 字符位置按 Unicode 字符（码点）计，与后端 Python 字符串下标一致。
 */
const props = defineProps<{ modelValue: boolean; taskId: string; files: TaskFile[]; items: RedactionItemPos[] }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 已补充成功，返回最新的脱敏信息 */
  (e: 'done', info: RedactionInfo): void
  (e: 'stale'): void
}>()

const categoryOptions = toOptions(USER_CATEGORY_OPTIONS)
const visible = computed({ get: () => props.modelValue, set: (v) => emit('update:modelValue', v) })

interface FileNodes {
  loading: boolean
  error: string
  available: boolean
  nodes: PreviewNode[]
}
const states = reactive<Record<string, FileNodes>>({})
const activeId = ref('')

const sortedFiles = computed(() =>
  [...props.files].sort((a, b) => Number(b.is_main) - Number(a.is_main) || a.seq - b.seq),
)

async function loadFile(id: string) {
  if (states[id]) return
  const st = reactive<FileNodes>({ loading: true, error: '', available: true, nodes: [] })
  states[id] = st
  try {
    const res = await previewApi.nodes(props.taskId, id)
    st.available = res.text_available
    st.nodes = res.items.filter((n) => n.part === 'body' && n.text)
  } catch (e) {
    st.error = e instanceof ApiError ? e.message : '加载失败，请重试'
  } finally {
    st.loading = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    clearSel()
    if (!activeId.value) activeId.value = sortedFiles.value[0]?.id ?? ''
    if (activeId.value) void loadFile(activeId.value)
  },
)
watch(activeId, (id) => {
  clearSel()
  if (id && props.modelValue) void loadFile(id)
})

// ---- 节点文字按已替换的片段拆分（码点位置） ----
const itemsByLoc = computed(() => {
  const m = new Map<string, RedactionItemPos[]>()
  for (const it of props.items) {
    const list = m.get(it.loc) ?? []
    list.push(it)
    m.set(it.loc, list)
  }
  for (const list of m.values()) list.sort((a, b) => a.char_start - b.char_start)
  return m
})

function segments(n: PreviewNode): CompareSeg[] {
  return splitOriginal(n.text ?? '', itemsByLoc.value.get(n.loc) ?? [])
}

function clauseOf(n: PreviewNode): string {
  return n.clause_no ?? n.cell_ref ?? ''
}

// ---- 选择 ----
interface Sel {
  loc: string
  start: number
  end: number
  text: string
}
const sel = ref<Sel | null>(null)
const selNotice = ref('')
const category = ref<UserCategory>('ORG')
const saving = ref(false)
const saveError = ref('')

function clearSel() {
  sel.value = null
  selNotice.value = ''
  saveError.value = ''
}

function cpLen(s: string): number {
  return Array.from(s).length
}

function onMouseUp() {
  const s = window.getSelection()
  if (!s || s.rangeCount === 0 || s.isCollapsed) return
  const range = s.getRangeAt(0)
  const holder = (n: Node) => (n.nodeType === 1 ? (n as Element) : n.parentElement)?.closest<HTMLElement>('.txt') ?? null
  const a = holder(range.startContainer)
  const b = holder(range.endContainer)
  clearSel()
  if (!a && !b) return
  if (!a || !b || a !== b) {
    selNotice.value = '请在同一段文字内选择'
    return
  }
  const pre = document.createRange()
  pre.selectNodeContents(a)
  pre.setEnd(range.startContainer, range.startOffset)
  const full = a.textContent ?? ''
  const u16Start = pre.toString().length
  const picked = range.toString()
  const start = cpLen(full.slice(0, u16Start))
  const end = start + cpLen(picked)
  const loc = a.dataset.loc ?? ''
  const overlap = (itemsByLoc.value.get(loc) ?? []).some((it) => it.char_start < end && it.char_end > start)
  if (overlap) {
    selNotice.value = '所选文字已被替换'
    return
  }
  const trimmed = picked.trim()
  if (cpLen(trimmed) < 2 || cpLen(trimmed) > 100) {
    selNotice.value = '请选择 2–100 个字符'
    return
  }
  sel.value = { loc, start, end, text: picked }
}

async function submit() {
  if (!sel.value) return
  saving.value = true
  saveError.value = ''
  try {
    const info = await redactionApi.manual(props.taskId, {
      loc: sel.value.loc,
      char_start: sel.value.start,
      char_end: sel.value.end,
      category: category.value,
    })
    window.getSelection()?.removeAllRanges()
    clearSel()
    emit('done', info)
  } catch (e) {
    if (e instanceof ApiError) {
      saveError.value = REDACTION_ERROR_MESSAGES[e.code] ?? e.message
      if (e.code === 'TASK_STATE_CONFLICT') emit('stale')
    } else {
      saveError.value = '操作失败，请重试'
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="在原文中选择" width="80%" top="24px" class="manual-dialog" append-to-body>
    <p class="hint">在同一段文字内选中要替换的文字，再选择类别。已替换的文字带底色，悬停可看到代号；相同的文字会在全文一并替换。</p>

    <el-tabs v-model="activeId">
      <el-tab-pane v-for="f in sortedFiles" :key="f.id" :label="f.original_name" :name="f.id" />
    </el-tabs>

    <div v-if="states[activeId]" v-loading="states[activeId].loading" class="body" @mouseup="onMouseUp">
      <el-alert v-if="states[activeId].error" type="error" show-icon :closable="false" :title="states[activeId].error" />
      <el-empty
        v-else-if="!states[activeId].loading && !states[activeId].available"
        description="原文已按保留期清理，不能再选择文字"
      />
      <el-empty v-else-if="!states[activeId].loading && !states[activeId].nodes.length" description="没有可选择的正文" />
      <div v-for="n in states[activeId].nodes" :key="n.loc" class="node">
        <span v-if="clauseOf(n)" class="clause">{{ clauseOf(n) }}</span>
        <span class="txt" :data-loc="n.loc"><template v-for="(g, i) in segments(n)" :key="i"><span v-if="g.placeholder" class="done" :class="`kind-${REDACTION_KIND[g.category ?? ''] ?? 'other'}`" :title="g.placeholder">{{ g.text }}</span><template v-else>{{ g.text }}</template></template></span>
      </div>
    </div>

    <el-alert v-if="selNotice" type="warning" show-icon :closable="false" :title="selNotice" class="gap" />
    <div v-if="sel" class="pick gap">
      <div class="picked">已选：{{ sel.text }}</div>
      <el-radio-group v-model="category">
        <el-radio v-for="o in categoryOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
      </el-radio-group>
      <el-button type="primary" :loading="saving" @click="submit">设为敏感信息</el-button>
    </div>
    <el-alert v-if="saveError" type="error" show-icon :closable="false" :title="saveError" class="gap" />

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.manual-dialog) {
  /* 总高度随视口约束，正文收缩滚动，避免默认 15vh 顶距把底部操作推出窗口。 */
  max-height: calc(100vh - 48px);
  margin-bottom: 24px;
  display: flex;
  flex-direction: column;
}
:global(.manual-dialog .el-dialog__header),
:global(.manual-dialog .el-dialog__footer) { flex: none; }
:global(.manual-dialog .el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
:global(.manual-dialog .el-tabs),
:global(.manual-dialog .el-alert),
.manual-dialog .hint,
.pick { flex-shrink: 0; }
.body {
  min-height: 120px;
  max-height: 50vh;
  flex: 1 1 auto;
  overflow: auto;
  padding: 24px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
}
.node {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.clause {
  flex: none;
  min-width: 48px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.txt {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
}
.done {
  background: var(--kind-bg);
  border-bottom: 2px solid var(--kind-color);
}
.pick {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.picked {
  flex: 1 0 100%;
  min-width: 0;
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-all;
}
.kind-org { --kind-bg: var(--si-k-org-bg); --kind-color: var(--si-k-org); }
.kind-person { --kind-bg: var(--si-k-person-bg); --kind-color: var(--si-k-person); }
.kind-contact { --kind-bg: var(--si-k-contact-bg); --kind-color: var(--si-k-contact); }
.kind-id { --kind-bg: var(--si-k-id-bg); --kind-color: var(--si-k-id); }
.kind-addr { --kind-bg: var(--si-k-addr-bg); --kind-color: var(--si-k-addr); }
.kind-other { --kind-bg: var(--si-k-other-bg); --kind-color: var(--si-k-other); }
</style>
