<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { CopyDocument } from '@element-plus/icons-vue'
import { computed, ref, watch } from 'vue'
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

const props = defineProps<{ finding: Finding; selected?: boolean }>()
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
      <span class="title">{{ finding.title }}</span>
      <span v-if="finding.location" class="location">{{ finding.location }}</span>
    </div>
    <div class="badges">
      <el-tag :type="SEVERITY_TAG[finding.severity] ?? 'info'" effect="dark" size="small">
        {{ severityLabel(finding.severity) }}
      </el-tag>
      <el-tag size="small" type="info">{{ categoryLabel(finding.category) }}</el-tag>
      <el-tag :type="CITE_TAG[finding.cite_status] ?? 'info'" size="small" effect="plain">
        {{ citeStatusLabel(finding.cite_status) }}
      </el-tag>
    </div>

    <div class="row">
      <span class="label">原文引用</span>
      <span v-if="finding.quote" class="quote">{{ finding.quote }}</span>
      <span v-else class="muted">（缺失型问题，没有对应的原文）</span>
    </div>
    <div class="row">
      <span class="label">问题说明</span><span class="text">{{ finding.problem }}</span>
    </div>
    <div v-if="finding.impact" class="row">
      <span class="label">对我方的影响</span><span class="text">{{ finding.impact }}</span>
    </div>
    <div v-if="finding.suggestion || finding.suggested_text" class="suggestions">
    <div v-if="finding.suggestion" class="row">
      <span class="label">修改建议</span><span class="text">{{ finding.suggestion }}</span>
    </div>
    <div v-if="finding.suggested_text" class="row">
      <span class="label">可直接替换的措辞</span>
      <span class="text replace">{{ finding.suggested_text }}</span>
      <el-button size="small" :icon="CopyDocument" @click="copy(finding.suggested_text)">复制</el-button>
    </div>
    </div>
    <div v-if="finding.basis" class="row basis">
      <span class="label">依据</span><span class="text">{{ finding.basis }}</span>
    </div>

    <div class="handling">
      <el-select v-model="status" size="small" class="status" :disabled="saving" @change="save({ handling_status: status })">
        <el-option v-for="o in handlingOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-input
        v-model="note"
        size="small"
        maxlength="500"
        placeholder="备注（选填）"
        :disabled="saving"
        @blur="saveNote"
        @keyup.enter="saveNote"
      />
    </div>
  </el-card>
</template>

<style scoped>
.card {
  margin-bottom: 12px;
  position: relative;
  border-left: 4px solid var(--el-color-info);
}
.card.severity-high { border-left-color: var(--el-color-danger); }
.card.severity-medium { border-left-color: var(--el-color-warning); }
.card :deep(.el-card__body) { padding: 20px 24px; }
.card.locatable {
  cursor: pointer;
}
.card.selected {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 3px var(--el-color-primary-light-9);
}
.head, .badges {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.location { margin-left: auto; font-size: 12px; color: var(--el-text-color-secondary); }
.title {
  font-weight: 600;
  font-size: 16px;
  margin-right: 4px;
}
.row {
  margin: 12px 0;
  line-height: 1.6;
}
.label {
  display: block;
  margin-bottom: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.text,
.quote {
  display: block;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.quote {
  padding: 12px;
  background: var(--el-fill-color-light);
  border-left: 3px solid var(--el-border-color);
  color: var(--el-text-color-regular);
}
.replace {
  background: var(--el-bg-color);
  border: 1px dashed var(--el-border-color);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 8px;
}
.suggestions { background: var(--el-color-primary-light-9); padding: 4px 16px; border-radius: 8px; margin-top: 16px; }
.basis { font-size: 12px; color: var(--el-text-color-secondary); }
.muted {
  color: var(--el-text-color-secondary);
}
.handling {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-light);
}
.status {
  flex: none;
  width: 110px;
}
</style>
