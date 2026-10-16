<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { runsApi, type Finding, type HandlingStatus } from '@/api/runs'
import {
  HANDLING_OPTIONS,
  categoryLabel,
  citeStatusLabel,
  severityLabel,
  toOptions,
} from '@/utils/labels'

const props = defineProps<{ finding: Finding }>()
const emit = defineEmits<{ (e: 'updated', f: Finding): void }>()

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

const SEVERITY_TAG: Record<string, 'danger' | 'warning' | 'info'> = {
  high: 'danger',
  medium: 'warning',
  low: 'info',
}
const CITE_TAG: Record<string, 'success' | 'warning' | 'info'> = {
  verified: 'success',
  unverified: 'warning',
  missing: 'info',
}

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
  <el-card shadow="never" class="card">
    <div class="head">
      <span class="title">{{ finding.title }}</span>
      <el-tag :type="SEVERITY_TAG[finding.severity] ?? 'info'" effect="dark" size="small">
        {{ severityLabel(finding.severity) }}
      </el-tag>
      <el-tag size="small" type="info">{{ categoryLabel(finding.category) }}</el-tag>
      <el-tag :type="CITE_TAG[finding.cite_status] ?? 'info'" size="small" effect="plain">
        {{ citeStatusLabel(finding.cite_status) }}
      </el-tag>
    </div>

    <div v-if="finding.location" class="row">
      <span class="label">位置</span><span>{{ finding.location }}</span>
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
    <div v-if="finding.suggestion" class="row">
      <span class="label">修改建议</span><span class="text">{{ finding.suggestion }}</span>
    </div>
    <div v-if="finding.suggested_text" class="row">
      <span class="label">可直接替换的措辞</span>
      <span class="text replace">{{ finding.suggested_text }}</span>
      <el-button size="small" @click="copy(finding.suggested_text)">复制</el-button>
    </div>
    <div v-if="finding.basis" class="row">
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
}
.head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.title {
  font-weight: 600;
  font-size: 15px;
  margin-right: 4px;
}
.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 6px 0;
  line-height: 1.6;
}
.label {
  flex: none;
  width: 110px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.text,
.quote {
  flex: 1;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.quote {
  padding-left: 8px;
  border-left: 3px solid var(--el-border-color);
  color: var(--el-text-color-regular);
}
.replace {
  background: var(--el-fill-color-light);
  padding: 4px 8px;
}
.muted {
  color: var(--el-text-color-secondary);
}
.handling {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.status {
  flex: none;
  width: 110px;
}
</style>
