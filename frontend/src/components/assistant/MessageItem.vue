<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { saveBlob } from '@/utils/save'
import type { Message } from '@/api/assistant'
import { assistantApi } from '@/api/assistant'
import { ApiError } from '@/api/http'
import CalcResult from '@/components/result/CalcResult.vue'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import { markTokens, unresolvedCount } from '@/utils/marks'
import { markKindLabel } from '@/utils/labels'
import { inline, markdown } from '@/utils/markdown'
import { formatDateTime } from '@/utils/time'
import { assistantStatusLabel } from '@/utils/labels'
import CompareTable from './CompareTable.vue'
import TranslationView from './TranslationView.vue'
const props = defineProps<{ message: Message; sessionId: string; last?: boolean }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number] }>()
const tokens = computed(() => markTokens(props.message.content, props.message.marks))
const tokenSet = computed(() => new Set(tokens.value.keys()))
const count = computed(() => unresolvedCount(props.message.marks))
const parsed = computed(() => {
  const calcs = [...(props.message.calcs ?? [])].sort((a,b) => a.line_start - b.line_start)
  let index = 0
  return markdown(props.message.content).map(block => ({ ...block, calc: block.kind === 'paragraph' && block.text.startsWith('【算式】') ? calcs[index++] : undefined }))
})
const pending = computed(() => ['queued', 'streaming'].includes(props.message.status))
const hasTable = computed(() => !!props.message.compare || parsed.value.some(block => block.kind === 'table'))
const downloading = ref(false), error = ref('')
async function download(format: 'docx' | 'xlsx', variant = 'translation') {
  downloading.value = true; error.value = ''
  try {
    const { blob, filename } = await assistantApi.download(props.sessionId, props.message.id, format, variant)
    await saveBlob(blob, filename)
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
  <article class="message" :class="message.role === 'user' ? 'mine' : 'reply'">
    <div class="hint meta">{{ message.role === 'user' ? '你' : '助手' }} · <span class="num">{{ formatDateTime(message.created_at) }}</span><template v-if="message.role === 'assistant' && !pending"> · {{ assistantStatusLabel(message.status) }}</template></div>
    <ZoneHeader v-if="message.role === 'assistant'" kind="ai" />
    <CompareTable v-if="message.compare" :comparison="message.compare" />
    <TranslationView v-else-if="message.template === 'translate' && message.role === 'assistant'" :message="message" :session-id="sessionId" />
    <div v-else class="markdown">
      <template v-for="(block, index) in parsed" :key="index">
        <CalcResult v-if="block.calc" :calc="block.calc" />
        <hr v-else-if="block.kind === 'rule'">
        <div v-else-if="block.kind === 'table'" class="table-scroll"><table><thead><tr><th v-for="(header, col) in block.headers" :key="col"><template v-for="(part, pi) in inline(header, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></th></tr></thead><tbody><tr v-for="(row, ri) in block.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci"><template v-for="(part, pi) in inline(cell, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></td></tr></tbody></table></div>
        <p v-else :class="{ heading: block.kind === 'heading', list: block.kind === 'list' }"><span v-if="block.kind === 'list'">• </span><template v-for="(part, pi) in inline(block.text, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></p>
      </template>
    </div>
    <p v-if="pending" class="generating" role="status">正在生成…</p>
    <p v-if="count" class="warning">有 {{ count }} 处未能还原，已原样标出</p>
    <el-alert v-if="message.error" :title="message.error" type="error" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="message.role === 'assistant' && message.extras.kb_sent !== undefined" class="hint">附带公司知识库片段 {{ message.extras.kb_sent }} 段（{{ message.extras.kb_dropped ?? 0 }} 段因含疑似敏感信息未发送）</p>
    <p v-for="source in message.extras.history_sources" :key="source.task_id" class="hint"><router-link :to="`/review/${source.task_id}`" target="_blank" rel="noopener">来自 {{ source.month }} 的一份合同</router-link></p>
    <p v-if="message.role === 'assistant' && message.model_label" class="hint">{{ message.model_label }}</p>
    <div v-if="message.role === 'assistant'" class="message-actions" :class="{ 'on-hover': !pending && message.status !== 'failed' && !last }">
      <el-button v-if="pending" size="small" @click="emit('stop', message.id)">停止</el-button>
      <el-button v-else-if="message.status === 'failed'" size="small" @click="emit('retry', message.id)">重试</el-button>
      <template v-else><el-button link @click="copy">复制</el-button><el-button link :loading="downloading" @click="download('docx')">导出 Word</el-button><el-button v-if="message.template === 'translate'" link :disabled="downloading" @click="download('docx', 'bilingual')">导出对照 Word</el-button><el-button v-if="hasTable" link :disabled="downloading" @click="download('xlsx')">导出 Excel</el-button></template>
    </div>
  </article>
</template>
<style scoped>
.message { padding: 16px 0; }
/* 用户消息：靠右气泡；助手回复：满宽、无边框无底色 */
.mine { display: flex; flex-direction: column; align-items: flex-end; }
.mine .markdown { max-width: 80%; box-sizing: border-box; padding: 12px 16px; background: var(--el-color-primary-light-9); border-radius: var(--si-radius-card); }
.mine .markdown p:first-child { margin-top: 0; }
.mine .markdown p:last-child { margin-bottom: 0; }
.meta { margin-bottom: 4px; }
.markdown { font: var(--si-font-reading); color: var(--si-text-reading); overflow-wrap: anywhere; white-space: pre-wrap; }
.markdown p { margin: 12px 0; }
.heading { font: var(--si-font-h3); }
strong { font-weight: 600; }
.list { padding-left: 16px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font: var(--si-font-body); }
td, th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--el-border-color-light); }
th { background: var(--el-fill-color-light); }
.warning { color: var(--el-color-warning); font: var(--si-font-meta); }
.generating { margin: 4px 0 0; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.message-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.message-actions :deep(.el-button) { margin-left: 0; }
/* 悬停该条回复或键盘聚焦其中时显示操作；保持占位，不跳动 */
.message-actions.on-hover { opacity: 0; transition: opacity 150ms; }
.reply:hover .message-actions.on-hover, .reply:focus-within .message-actions.on-hover { opacity: 1; }
.si-unresolved { color: inherit; background: var(--si-unresolved-bg); text-decoration: underline dashed 1px var(--si-warn-ink); text-underline-offset: 3px; cursor: help; }
</style>
