<script setup lang="ts">
import { ElMessage, type InputInstance } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { runsApi, type Finding, type HandlingStatus } from '@/api/runs'
import {
  CITE_TAG,
  HANDLING_OPTIONS,
  SEVERITY_TAG,
  categoryLabel,
  citeStatusLabel,
  severityLabel,
  toOptions,
} from '@/utils/labels'

const props = defineProps<{ finding: Finding; no: string; selected?: boolean }>()
const emit = defineEmits<{
  (e: 'updated', f: Finding): void
  (e: 'select', f: Finding): void
}>()

// 缺失型问题（locs 为空）没有对应位置，不可点击
const locatable = computed(() => props.finding.locs.length > 0)
function onSelect() {
  if (locatable.value) emit('select', props.finding)
}

const handlingOptions = toOptions(HANDLING_OPTIONS)
const status = ref<HandlingStatus>(props.finding.handling_status)
const note = ref(props.finding.handling_note ?? '')
const saving = ref(false)
const editingNote = ref(false)
const noteInput = ref<InputInstance>()

async function editNote() {
  editingNote.value = true
  await nextTick()
  noteInput.value?.focus()
}

watch(
  () => props.finding,
  (f) => {
    status.value = f.handling_status
    note.value = f.handling_note ?? ''
  },
)

async function save(body: { handling_status?: HandlingStatus; handling_note?: string | null }) {
  saving.value = true
  try {
    const updated = await runsApi.patchFinding(props.finding.id, body)
    emit('updated', updated)
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    // 恢复为已保存的值
    status.value = props.finding.handling_status
    note.value = props.finding.handling_note ?? ''
  } finally {
    saving.value = false
  }
}

function saveNote() {
  if (!editingNote.value) return
  editingNote.value = false
  const text = note.value.trim()
  if (text === (props.finding.handling_note ?? '')) return
  void save({ handling_note: text })
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
    if (!ok) {
      ElMessage.error('复制失败，请手动选择文字复制')
      return
    }
  }
  ElMessage.success('已复制')
}
</script>

<template>
  <el-card shadow="never" class="card" :class="[{ locatable, selected }, `severity-${finding.severity}`]" :tabindex="locatable ? 0 : undefined" @click="onSelect" @keydown.enter.self.prevent="onSelect" @keydown.space.self.prevent="onSelect">
    <div class="head">
      <span class="number num">{{ no }}</span>
      <el-tag :type="SEVERITY_TAG[finding.severity] ?? 'info'" effect="dark" size="small">{{ severityLabel(finding.severity) }}</el-tag>
      <span class="title">{{ finding.title }}</span>
      <span v-if="finding.location" class="location">{{ finding.location }}</span>
    </div>
    <div class="badges">
      <span>{{ categoryLabel(finding.category) }}</span>
      <el-tag v-if="finding.source === 'template'" size="small" type="info" effect="plain">范本对照</el-tag>
      <el-tag v-if="finding.law_unverified" size="small" type="warning" effect="plain">引用未经法规库核实</el-tag>
      <el-tag :type="CITE_TAG[finding.cite_status] ?? 'info'" size="small" effect="plain">
        {{ citeStatusLabel(finding.cite_status) }}
      </el-tag>
    </div>

    <div class="row"><span v-if="finding.quote" class="quote">{{ finding.quote }}</span><span v-else class="muted">缺失型问题，没有对应的原文</span></div>
    <div class="row">
      <span class="label">问题</span><span class="text">{{ finding.problem }}</span>
    </div>
    <div v-if="finding.impact" class="row">
      <span class="label">影响</span><span class="text">{{ finding.impact }}</span>
    </div>
    <div v-if="finding.suggestion || finding.suggested_text" class="suggestions">
    <div v-if="finding.suggestion" class="row">
      <span class="label">建议</span><span class="text">{{ finding.suggestion }}</span>
    </div>
    <div v-if="finding.suggested_text" class="replacement">
      <span class="label">可直接替换的措辞</span>
      <el-button link size="small" :icon="CopyDocument" @click.stop="copy(finding.suggested_text)">复制</el-button>
      <span class="text replace">{{ finding.suggested_text }}</span>
    </div>
    </div>
    <div v-if="finding.basis" class="row basis">
      <span class="label">依据</span><span class="text">{{ finding.basis }}</span>
    </div>

    <el-collapse v-if="finding.law_refs?.length" @click.stop>
      <el-collapse-item v-for="(law, index) in finding.law_refs" :key="index" :name="index" :title="`依据：《${law.title}》${law.article_no ?? ''}`">
        <p class="text">{{ law.text }}</p>
      </el-collapse-item>
    </el-collapse>
    <div class="handling" @click.stop>
      <el-select v-model="status" size="small" class="status" :disabled="saving" @change="save({ handling_status: status })">
        <el-option v-for="o in handlingOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-input
        v-if="editingNote" ref="noteInput" v-model="note"
        size="small"
        maxlength="500"
        placeholder="备注（选填）"
        :disabled="saving"
        @blur="saveNote"
        @keyup.enter="saveNote"
      />
      <div v-else class="note"><span v-if="finding.handling_note">备注：{{ finding.handling_note }}</span><el-button link type="primary" size="small" :disabled="saving" @click="editNote">{{ finding.handling_note ? '修改' : '+ 添加备注' }}</el-button></div>
    </div>
  </el-card>
</template>

<style scoped>
.card { margin-bottom: 12px; position: relative; transition: border-color 150ms; }
.card::before { content: ''; position: absolute; left: 0; top: 16px; bottom: 16px; width: 3px; border-top-right-radius: 2px; border-bottom-right-radius: 2px; background: var(--el-color-info); }
.card.severity-high::before { background: var(--el-color-danger); }
.card.severity-medium::before { background: var(--el-color-warning); }
.card :deep(.el-card__body) { padding: 8px 20px; }
.card.locatable { cursor: pointer; }
.card.locatable:hover { border-color: var(--si-card-hover-border); }
.card.selected { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary); }
.head, .badges { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }
.number, .badges { color: var(--el-text-color-secondary); font: var(--si-font-meta); }
.location { margin-left: auto; color: var(--el-text-color-regular); font: var(--si-font-meta); }
.title { font: var(--si-font-h3); }
.row { margin: 4px 0; line-height: 1.6; }
.label { margin-right: 8px; color: var(--el-text-color-secondary); font: var(--si-font-meta); }
.text { white-space: pre-wrap; overflow-wrap: anywhere; }
.quote { display: block; padding: 4px 12px; background: var(--el-fill-color-light); border-left: 3px solid var(--el-border-color); color: var(--el-text-color-regular); white-space: pre-wrap; overflow-wrap: anywhere; }
.replacement { position: relative; padding: 8px 12px; background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 8px; }
.replacement .el-button { float: right; }
.replace { display: block; }
.suggestions { background: var(--el-color-primary-light-9); padding: 4px 12px; border-radius: 8px; margin-top: 4px; }
.suggestions .row { margin: 0; }
.basis, .muted { color: var(--el-text-color-secondary); font: var(--si-font-caption); }
.handling { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--el-border-color-light); }
.status { flex: none; width: 104px; }
.note { display: flex; align-items: center; gap: 8px; min-width: 0; overflow-wrap: anywhere; font: var(--si-font-meta); }
</style>
