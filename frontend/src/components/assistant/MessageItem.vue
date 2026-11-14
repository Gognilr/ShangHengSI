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
import { useStale } from '@/utils/stale'
import { assistantStatusLabel } from '@/utils/labels'
import CompareTable from './CompareTable.vue'
import TranslationView from './TranslationView.vue'
/** savedNo：这条回复已保存为问题 savedNo 的修改建议（V321-E-02） */
const props = defineProps<{ message: Message; sessionId: string; last?: boolean; draftable?: boolean; savedNo?: string; reading?: boolean }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number]; draft: [message: Message] }>()
const tokens = computed(() => markTokens(props.message.content, props.message.marks))
const tokenSet = computed(() => new Set(tokens.value.keys()))
const count = computed(() => unresolvedCount(props.message.marks))
const parsed = computed(() => {
  const calcs = [...(props.message.calcs ?? [])].sort((a,b) => a.line_start - b.line_start)
  let index = 0
  return props.message.content.split(/(【修改条文】[\s\S]*?(?:【\/修改条文】|$))/g).flatMap(segment => {
    const clause = segment.startsWith('【修改条文】')
    const text = clause ? segment.replace(/^【修改条文】/, '').replace(/【\/修改条文】$/, '') : segment
    return markdown(text).map(block => ({ ...block, clause, calc: block.kind === 'paragraph' && block.text.startsWith('【算式】') ? calcs[index++] : undefined }))
  })
})
const pending = computed(() => ['queued', 'streaming'].includes(props.message.status))
// 等待写法（V321-E-12）：排队写“等待模型响应”；输出中用正文末尾光标；等首字过久补一句
const streaming = computed(() => props.message.status === 'streaming' && !!props.message.content)
const slow = useStale(() => props.message.content.length === 0 && pending.value, 10000)
const reading = computed(() => !!props.reading)
const waiting = computed(() => {
  if (!pending.value || streaming.value) return ''
  const base = reading.value ? '正在读取附件' : '等待模型响应'
  return slow.value && !reading.value ? `${base}，模型响应较慢，可以先做别的` : base
})
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
        <pre v-else-if="block.kind === 'code'" class="code">{{ block.text }}</pre>
        <div v-else-if="block.kind === 'table'" class="table-scroll"><table><thead><tr><th v-for="(header, col) in block.headers" :key="col"><template v-for="(part, pi) in inline(header, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></th></tr></thead><tbody><tr v-for="(row, ri) in block.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci"><template v-for="(part, pi) in inline(cell, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template></td></tr></tbody></table></div>
        <p v-else :class="{ clause: block.clause, heading: block.kind === 'heading', list: block.kind === 'list' }" :style="block.kind === 'list' && block.level ? { marginLeft: `${block.level * 20}px` } : undefined"><span v-if="block.kind === 'list'">{{ block.order ? `${block.order}. ` : '• ' }}</span><template v-for="(part, pi) in inline(block.text, tokenSet)" :key="pi"><mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark><strong v-else-if="part.bold">{{ part.text }}</strong><span v-else>{{ part.text }}</span></template><i v-if="streaming && index === parsed.length - 1" class="cursor" aria-hidden="true" /></p>
      </template>
      <p v-if="streaming && parsed[parsed.length - 1]?.kind !== 'paragraph' && parsed[parsed.length - 1]?.kind !== 'heading' && parsed[parsed.length - 1]?.kind !== 'list'"><i class="cursor" aria-hidden="true" /></p>
    </div>
    <details v-if="message.role === 'user' && message.sent" class="sent">
      <summary class="hint">查看实际发送内容</summary>
      <pre>{{ message.sent }}</pre>
    </details>
    <p v-if="waiting" class="generating" role="status">{{ waiting }}</p>
    <p v-if="count" class="warning">有 {{ count }} 处未能还原，已原样标出</p>
    <el-alert v-if="message.error" :title="message.error" type="error" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <p v-if="message.role === 'assistant' && (Number(message.extras.kb_sent) > 0 || Number(message.extras.kb_dropped) > 0)" class="hint">附带公司知识库片段 {{ message.extras.kb_sent }} 段（{{ message.extras.kb_dropped ?? 0 }} 段因含疑似敏感信息未发送）</p>
    <p v-for="source in message.extras.history_sources" :key="source.task_id" class="hint"><router-link :to="`/review/${source.task_id}`" target="_blank" rel="noopener">来自 {{ source.month }} 的一份合同</router-link></p>
    <p v-if="message.role === 'assistant' && message.model_label" class="hint">{{ message.model_label }}</p>
    <div v-if="message.role === 'assistant'" class="message-actions" :class="{ 'on-hover': !pending && message.status !== 'failed' && !last }">
      <el-button v-if="pending" size="small" @click="emit('stop', message.id)">停止</el-button>
      <el-button v-else-if="message.status === 'failed'" size="small" @click="emit('retry', message.id)">重试</el-button>
      <template v-else><el-button link @click="copy">复制</el-button><el-button link :loading="downloading" @click="download('docx')">导出 Word</el-button><el-button v-if="message.template === 'translate'" link :disabled="downloading" @click="download('docx', 'bilingual')">导出对照 Word</el-button><el-button v-if="hasTable" link :disabled="downloading" @click="download('xlsx')">导出 Excel</el-button></template>
    </div>
    <!-- 按问题讨论的回复：保存为该问题的修改建议（V32-B-03） -->
    <div v-if="draftable && message.role === 'assistant' && message.finding_id && !pending && message.status !== 'failed'" class="draft-action">
      <template v-if="savedNo !== undefined"><span class="draft-saved" role="status">✓ 已保存为问题 {{ savedNo }} 的修改建议</span><el-button link type="primary" size="small" @click="emit('draft', message)">重新保存</el-button></template>
      <el-button v-else size="small" type="primary" plain @click="emit('draft', message)">保存到该问题的修改建议</el-button>
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
/* 用户消息下的“查看实际发送内容”：只读，代号文本原样显示 */
.sent { max-width: 80%; margin-top: 4px; text-align: right; }
.sent summary { cursor: pointer; }
.sent pre { margin: 6px 0 0; padding: 8px 12px; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-meta); color: var(--el-text-color-secondary); background: var(--el-fill-color-light); border-radius: var(--el-border-radius-base); }
.markdown { font: var(--si-font-reading); color: var(--si-text-reading); overflow-wrap: anywhere; white-space: pre-wrap; }
.markdown p { margin: 12px 0; }
.heading { font: var(--si-font-h3); }
.clause { padding: 12px 16px; border-left: 3px solid var(--si-blue); background: var(--el-color-primary-light-9); border-radius: var(--si-radius-paper); }
strong { font-weight: 600; }
.list { padding-left: 16px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font: var(--si-font-body); }
td, th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--el-border-color-light); }
th { background: var(--el-fill-color-light); }
.warning { color: var(--el-color-warning); font: var(--si-font-meta); }
.code { margin: 12px 0; padding: 10px 14px; overflow-x: auto; white-space: pre; font: var(--si-font-meta); background: var(--el-fill-color-light); border-radius: var(--el-border-radius-base); }
.cursor { display: inline-block; width: 2px; height: 1.1em; margin-left: 2px; vertical-align: text-bottom; background: var(--si-blue); animation: cursor-blink 1s steps(1) infinite; }
@keyframes cursor-blink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .cursor { animation: none; } }
.generating { margin: 4px 0 0; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.message-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.draft-action { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.draft-saved { color: var(--si-ok-ink); font: var(--si-font-label-sm); }
.message-actions :deep(.el-button) { margin-left: 0; }
/* 悬停该条回复或键盘聚焦其中时显示操作；保持占位，不跳动 */
.message-actions.on-hover { opacity: 0; transition: opacity var(--si-dur-1) var(--si-ease); }
.reply:hover .message-actions.on-hover, .reply:focus-within .message-actions.on-hover { opacity: 1; }
.si-unresolved { color: inherit; background: var(--si-unresolved-bg); text-decoration: underline dashed 1px var(--si-warn-ink); text-underline-offset: 3px; cursor: help; }
</style>
