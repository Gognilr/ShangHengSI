<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { Message } from '@/api/assistant'
import { assistantApi } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { inline, markdown } from '@/utils/markdown'
import { formatDateTime } from '@/utils/time'
import { assistantStatusLabel } from '@/utils/labels'
import CompareTable from './CompareTable.vue'
import TranslationView from './TranslationView.vue'
const props = defineProps<{ message: Message; sessionId: string }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number] }>()
const parsed = computed(() => markdown(props.message.content))
const pending = computed(() => ['queued', 'streaming'].includes(props.message.status))
const unknown = computed(() => /\[\[[^\]\n]+\]\]/.test(props.message.content))
const hasTable = computed(() => !!props.message.compare || parsed.value.some(block => block.kind === 'table'))
const downloading = ref(false), error = ref('')
async function download(format: 'docx' | 'xlsx', variant = 'translation') {
  downloading.value = true; error.value = ''
  try {
    const { blob, filename } = await assistantApi.download(props.sessionId, props.message.id, format, variant)
    const url = URL.createObjectURL(blob), a = document.createElement('a')
    a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { error.value = e instanceof ApiError ? e.message : '导出失败，请重试' }
  finally { downloading.value = false }
}
async function copy() {
  try { await navigator.clipboard.writeText(props.message.content) }
  catch {
    const area = document.createElement('textarea'); area.value = props.message.content
    area.className = 'sr-only'; document.body.append(area); area.select()
    const copied = document.execCommand('copy'); area.remove()
    if (!copied) { ElMessage.error('复制失败，请手动选择文字复制'); return }
  }
  ElMessage.success('已复制')
}
</script>
<template>
  <article class="message" :class="{ mine: message.role === 'user' }">
    <div class="hint">{{ message.role === 'user' ? '你' : '助手' }} · <span class="num">{{ formatDateTime(message.created_at) }}</span> · {{ assistantStatusLabel(message.status) }}</div>
    <CompareTable v-if="message.compare" :comparison="message.compare" />
    <TranslationView v-else-if="message.template === 'translate' && message.role === 'assistant'" :message="message" :session-id="sessionId" />
    <div v-else class="markdown">
      <template v-for="(block, index) in parsed" :key="index">
        <hr v-if="block.kind === 'rule'">
        <div v-else-if="block.kind === 'table'" class="table-scroll"><table><thead><tr><th v-for="(header, col) in block.headers" :key="col"><template v-for="(part, pi) in inline(header)" :key="pi"><el-tag v-if="part.unknown" type="warning" size="small">{{ part.text }}</el-tag><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></th></tr></thead><tbody><tr v-for="(row, ri) in block.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci"><template v-for="(part, pi) in inline(cell)" :key="pi"><el-tag v-if="part.unknown" type="warning" size="small">{{ part.text }}</el-tag><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></td></tr></tbody></table></div>
        <p v-else :class="{ heading: block.kind === 'heading', list: block.kind === 'list' }"><span v-if="block.kind === 'list'">• </span><template v-for="(part, pi) in inline(block.text)" :key="pi"><el-tag v-if="part.unknown" type="warning" size="small">{{ part.text }}</el-tag><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></p>
      </template>
    </div>
    <span v-if="pending" class="cursor" aria-label="正在生成">▍</span>
    <p v-if="unknown" class="warning">回复中有无法还原的代号</p>
    <el-alert v-if="message.error" :title="message.error" type="error" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="message.role === 'assistant' && message.extras.kb_sent !== undefined" class="hint">附带公司知识库片段 {{ message.extras.kb_sent }} 段（{{ message.extras.kb_dropped ?? 0 }} 段因含疑似敏感信息未发送）</p>
    <p v-for="source in message.extras.history_sources" :key="source.task_id" class="hint"><router-link :to="`/review/${source.task_id}`" target="_blank" rel="noopener">来自 {{ source.month }} 的一份合同</router-link></p>
    <div v-if="message.role === 'assistant'" class="message-actions">
      <el-button v-if="pending" size="small" @click="emit('stop', message.id)">停止</el-button>
      <el-button v-else-if="message.status === 'failed'" size="small" @click="emit('retry', message.id)">重试</el-button>
      <template v-else><el-button link @click="copy">复制</el-button><el-button link :loading="downloading" @click="download('docx')">导出 Word</el-button><el-button v-if="message.template === 'translate'" link :disabled="downloading" @click="download('docx', 'bilingual')">导出对照 Word</el-button><el-button v-if="hasTable" link :disabled="downloading" @click="download('xlsx')">导出 Excel</el-button></template>
    </div>
  </article>
</template>
<style scoped>
.message { padding: 20px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.mine { background: var(--el-fill-color-light); padding: 16px; border-radius: var(--si-radius-card); margin-top: 16px; }
.markdown { font: var(--si-font-reading); overflow-wrap: anywhere; white-space: pre-wrap; }
.markdown p { margin: 12px 0; }
.heading { font: var(--si-font-h3); }
strong { font-weight: 600; }
.list { padding-left: 16px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font: var(--si-font-body); }
td, th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--el-border-color-light); }
th { background: var(--el-fill-color-light); }
.warning { color: var(--el-color-warning); font: var(--si-font-meta); }
.message-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.message-actions :deep(.el-button) { margin-left: 0; }
.cursor { color: var(--el-color-primary); animation: blink 1s steps(2) infinite; }
@keyframes blink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .cursor { animation: none; } }
</style>
