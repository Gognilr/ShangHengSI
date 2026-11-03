<script setup lang="ts">
import { ElMessage, type InputInstance } from 'element-plus'
import { Copy } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { runsApi, type Finding, type HandlingStatus } from '@/api/runs'
import MarkedText from '@/components/result/MarkedText.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { CITE_TAG, HANDLING_OPTIONS, SEVERITY_TAG, categoryLabel, citeStatusLabel, severityLabel, toOptions } from '@/utils/labels'

/** 问题卡片（V31-U3 3.5）：收起为一行摘要，选中时展开全文；模型文字一律经 MarkedText 纯文本渲染。 */
const props = defineProps<{ finding: Finding; no: string; selected?: boolean; askEnabled?: boolean }>()
const emit = defineEmits<{
  (e: 'updated', f: Finding): void
  (e: 'select', f: Finding): void
  (e: 'ask', prefill: string): void
}>()

const f = computed(() => props.finding)
const basisShort = computed(() => {
  const law = f.value.law_refs?.[0]
  if (law) return `《${law.title}》${law.article_no ?? ''}`
  const chars = Array.from(f.value.basis ?? '')
  return chars.length > 20 ? `${chars.slice(0, 20).join('')}…` : chars.join('')
})
const meta = computed(() => [categoryLabel(f.value.category), f.value.location, basisShort.value && `依据 ${basisShort.value}`].filter(Boolean).join(' · '))

const handlingOptions = toOptions(HANDLING_OPTIONS) as { value: HandlingStatus; label: string }[]
const status = ref<HandlingStatus>(props.finding.handling_status)
const note = ref(props.finding.handling_note ?? '')
const saving = ref(false)
const editingNote = ref(false)
const noteInput = ref<InputInstance>()
watch(() => props.finding, (v) => { status.value = v.handling_status; note.value = v.handling_note ?? '' })

async function save(body: { handling_status?: HandlingStatus; handling_note?: string | null }) {
  saving.value = true
  try {
    emit('updated', await runsApi.patchFinding(props.finding.id, body))
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    status.value = props.finding.handling_status
    note.value = props.finding.handling_note ?? ''
  } finally { saving.value = false }
}
function setStatus(value: HandlingStatus) {
  if (saving.value || value === props.finding.handling_status) return
  status.value = value
  void save({ handling_status: value })
}
async function editNote() { editingNote.value = true; await nextTick(); noteInput.value?.focus() }
function saveNote() {
  if (!editingNote.value) return
  editingNote.value = false
  const text = note.value.trim()
  if (text !== (props.finding.handling_note ?? '')) void save({ handling_note: text })
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 非安全上下文或被浏览器拒绝时的备用做法
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    if (!ok) { ElMessage.error('复制失败，请手动选择文字复制'); return }
  }
  ElMessage.success('已复制')
}
</script>

<template>
  <article class="card" :class="[`severity-${f.severity}`, { selected }]">
    <div class="head" role="button" tabindex="0" :aria-expanded="!!selected" @click="emit('select', f)" @keydown.enter.self.prevent="emit('select', f)" @keydown.space.self.prevent="emit('select', f)">
      <span class="no">{{ no }}</span>
      <div class="main">
        <div class="title"><MarkedText :text="f.title" :marks="f.marks.title" /><span v-if="f.unresolved > 0" class="unresolved">{{ f.unresolved }} 处未能还原</span></div>
        <div class="meta">{{ meta }}</div>
      </div>
      <el-tag :type="SEVERITY_TAG[f.severity] ?? 'info'" size="small" round>{{ severityLabel(f.severity) }}</el-tag>
      <i class="dot" :class="`dot-${f.handling_status}`" :title="HANDLING_OPTIONS[f.handling_status]" />
    </div>

    <div v-if="selected" class="body">
      <blockquote v-if="f.quote_excerpt || f.quote" class="quote">{{ f.quote_excerpt || f.quote }}</blockquote>
      <p v-else class="muted">缺失型问题，没有对应的原文</p>
      <div class="row"><span class="label">问题</span><MarkedText class="text" :text="f.problem" :marks="f.marks.problem" /></div>
      <div v-if="f.impact" class="row"><span class="label">对我方的影响</span><MarkedText class="text" :text="f.impact" :marks="f.marks.impact" /></div>
      <div v-if="f.suggestion" class="row"><span class="label">修改建议</span><MarkedText class="text" :text="f.suggestion" :marks="f.marks.suggestion" /></div>
      <div v-if="f.suggested_text" class="replacement">
        <div class="replacement-head">
          <span class="label">可直接替换的措辞</span>
          <span v-if="f.marks.suggested_text?.length" class="copy-warn">含未能还原的写法，复制后请核对</span>
          <el-button link size="small" @click="copy(f.suggested_text)"><template #icon><Copy :size="14" :stroke-width="1.75" /></template>复制</el-button>
        </div>
        <MarkedText class="text" :text="f.suggested_text" :marks="f.marks.suggested_text" />
      </div>
      <div v-if="f.basis" class="row basis"><span class="label">依据</span><MarkedText class="text" :text="f.basis" :marks="f.marks.basis" /></div>
      <el-collapse v-if="f.law_refs?.length">
        <el-collapse-item v-for="(law, index) in f.law_refs" :key="index" :name="index" :title="`依据：《${law.title}》${law.article_no ?? ''}`">
          <p class="text">{{ law.text }}</p>
        </el-collapse-item>
      </el-collapse>
      <div class="badges">
        <el-tag :type="CITE_TAG[f.cite_status] ?? 'info'" size="small" effect="plain">{{ citeStatusLabel(f.cite_status) }}</el-tag>
        <el-tag v-if="f.law_unverified" size="small" type="warning" effect="plain">引用未经法规库核实</el-tag>
        <el-tag v-if="f.source === 'template'" size="small" type="info" effect="plain">范本对照</el-tag>
      </div>
      <div class="handling">
        <SiSegmented :model-value="status" :options="handlingOptions" size="small" ariaLabel="处理状态" @update:model-value="setStatus($event as HandlingStatus)" />
        <el-button v-if="askEnabled" link type="primary" size="small" @click="emit('ask', `关于问题 ${no}「${f.title}」：`)">追问此问题</el-button>
      </div>
      <el-input v-if="editingNote" ref="noteInput" v-model="note" size="small" maxlength="500" placeholder="备注（选填）" :disabled="saving" @blur="saveNote" @keyup.enter="saveNote" />
      <div v-else class="note"><span v-if="f.handling_note">备注：{{ f.handling_note }}</span><el-button link type="primary" size="small" :disabled="saving" @click="editNote">{{ f.handling_note ? '修改' : '+ 添加备注' }}</el-button></div>
    </div>
  </article>
</template>

<style scoped>
.card { position: relative; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); transition: border-color 150ms, box-shadow 150ms; }
.card::before { content: ''; position: absolute; left: 0; top: 12px; bottom: 12px; width: 3px; border-radius: 0 2px 2px 0; background: var(--si-info); }
.card.severity-high::before { background: var(--si-danger); }
.card.severity-medium::before { background: var(--si-warn); }
.card:hover { border-color: var(--si-card-hover-border); }
.card.selected { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary); }
.head { display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px 10px 16px; cursor: pointer; border-radius: var(--si-radius-card); }
.head:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.no { flex: none; padding-top: 1px; font: var(--si-font-code-sm); color: var(--si-ink-3); }
.main { flex: 1; min-width: 0; }
.title { font: var(--si-font-h4); color: var(--el-text-color-primary); overflow-wrap: anywhere; }
.unresolved { margin-left: 6px; font: var(--si-font-label-sm); color: var(--si-warn-ink); }
.meta { margin-top: 2px; font: var(--si-font-caption); color: var(--si-ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { flex: none; width: 8px; height: 8px; margin-top: 7px; border-radius: 50%; }
.dot-adopted { background: var(--el-color-primary); }
.dot-ignored { background: var(--si-ink-3); }
.body { display: flex; flex-direction: column; gap: 8px; padding: 0 14px 12px 16px; }
.quote { margin: 0; padding: 6px 12px; border-left: 3px solid var(--si-hair-strong); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
.row { display: flex; flex-direction: column; gap: 2px; }
.label { font: var(--si-font-label-sm); color: var(--si-ink-3); }
.text { font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
.replacement { padding: 8px 12px; border: 1px solid var(--si-hair-strong); border-radius: 8px; background: var(--el-color-primary-light-9); }
.replacement-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.replacement-head .el-button { margin-left: auto; }
.copy-warn { font: var(--si-font-caption); color: var(--si-warn-ink); }
.basis .text, .muted { color: var(--si-ink-3); font: var(--si-font-meta); }
.muted { margin: 0; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }
.handling { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 8px; border-top: 1px solid var(--si-hair); }
.note { display: flex; align-items: center; gap: 8px; min-width: 0; overflow-wrap: anywhere; font: var(--si-font-meta); }
</style>
