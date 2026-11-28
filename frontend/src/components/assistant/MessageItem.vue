<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChevronDown, Cpu, Send, ShieldCheck, Sparkle } from '@lucide/vue'
import { saveBlob } from '@/utils/save'
import type { Message, MessageSource } from '@/api/assistant'
import { assistantApi } from '@/api/assistant'
import { ApiError } from '@/api/http'
import CalcResult from '@/components/result/CalcResult.vue'
import SiZone from '@/components/ui/SiZone.vue'
import { markTokens, unresolvedCount } from '@/utils/marks'
import { markdown } from '@/utils/markdown'
import InlineText from './InlineText.vue'
import { formatDateTime } from '@/utils/time'
import { useStale } from '@/utils/stale'
import { assistantStatusLabel } from '@/utils/labels'
import CompareTable from './CompareTable.vue'
import CrosscheckTable from './CrosscheckTable.vue'
import TranslationView from './TranslationView.vue'
/** savedNo：这条回复已保存为问题 savedNo 的修改建议（V321-E-02）；sent：这条回答对应的提问实际发给模型的文字；codeCount：本会话遮住的处数 */
const props = defineProps<{ message: Message; sessionId: string; last?: boolean; draftable?: boolean; savedNo?: string; reading?: boolean; sent?: string; codeCount?: number }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number]; draft: [message: Message]; cite: [message: Message, source: MessageSource, anchor: HTMLElement] }>()
const tokens = computed(() => markTokens(props.message.content, props.message.marks))
// 回复依据（V321-E-13）：锚点只来自后端 cites（本地登记过的 id），模型自写的编号对不上就是普通文字
const cites = computed(() => new Map((props.message.cites ?? []).map(c => [c.token, c.ref])))
const sourceMap = computed(() => new Map((props.message.sources ?? []).map(src => [src.ref, src])))
const sources = computed(() => props.message.sources ?? [])
const kindLabel = { para: '原文', kb: '知识库', law: '法条' } as const
function cite(source: MessageSource, anchor: HTMLElement) { emit('cite', props.message, source, anchor) }
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
const streaming = computed(() => props.message.status === 'streaming' && !!props.message.content)
const slow = useStale(() => props.message.content.length === 0 && pending.value, 10000)
const reading = computed(() => !!props.reading)
// 等待（澄明 8.3）：本机读取附件用青色文字、不出动点；等模型首字用紫色动点“模型正在思考”；首字到达后由正文末尾光标接手
// 译稿由对照行自己显示“正在译 · n 秒”，不再另出动点
const waiting = computed(() => {
  if (!pending.value || streaming.value) return ''
  if (reading.value) return '正在本机读取附件'
  return props.message.template === 'translate' ? '' : '模型正在思考'
})
const waitingTail = computed(() => (waiting.value && slow.value && !reading.value ? '，响应较慢，可以先做别的' : ''))
// 本机给出的回复（单证核对、本地翻译等没有调用云端模型的）用青色徽标；其余为灵紫“模型 · 只见遮住后的文字”
const local = computed(() => !!props.message.crosscheck || (!pending.value && !props.message.model_label) || !!props.message.model_label?.startsWith('本地'))
const unknownTranslation = computed(() => props.message.template === 'translate' && !['local', 'cloud'].includes(String(props.message.params.engine)))
const showStatus = computed(() => !pending.value && !['done', 'active'].includes(props.message.status))
// 外发回执（澄明二版 6.5）：合上时一行“遮住 n 处 · 复检通过 · 模型名”，翻开是实际发出的提问文字（后端返回，按纯文本显示）
const receipt = computed(() => props.message.role === 'assistant' && !local.value && !pending.value && props.message.status !== 'failed' && !!props.sent)
const receiptOpen = ref(false)
const hasTable = computed(() => !!props.message.compare || !!props.message.crosscheck || parsed.value.some(block => block.kind === 'table'))
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
    <div v-if="message.role === 'assistant'" class="ai-line">
      <span v-if="unknownTranslation" class="aux">翻译方式未记录</span>
      <span v-else-if="local" class="local-tag"><Cpu :size="13" :stroke-width="2" />{{ message.model_label || '本机处理' }} · 不外发</span>
      <span v-else class="ai-tag"><Sparkle :size="13" :stroke-width="2" />{{ message.model_label || '模型' }} · 只见遮住后的文字</span>
      <span class="aux num">{{ formatDateTime(message.created_at) }}<template v-if="showStatus"> · {{ assistantStatusLabel(message.status) }}</template></span>
    </div>
    <CompareTable v-if="message.compare" :comparison="message.compare" />
    <CrosscheckTable v-else-if="message.crosscheck" :result="message.crosscheck" />
    <TranslationView v-else-if="message.template === 'translate' && message.role === 'assistant'" :message="message" :session-id="sessionId" />
    <component :is="message.role === 'assistant' && !local ? SiZone : 'div'" v-else-if="message.role === 'user' || message.content" :class="message.role === 'user' ? 'bubble' : local ? 'local-sec' : 'ai-zone'" v-bind="message.role === 'assistant' && !local ? { kind: 'ai' } : {}">
      <div class="markdown">
        <template v-for="(block, index) in parsed" :key="index">
          <CalcResult v-if="block.calc" :calc="block.calc" />
          <hr v-else-if="block.kind === 'rule'">
          <pre v-else-if="block.kind === 'code'" class="code">{{ block.text }}</pre>
          <div v-else-if="block.kind === 'table'" class="table-scroll"><table><thead><tr><th v-for="(header, col) in block.headers" :key="col"><InlineText :text="header" :tokens="tokens" :cites="cites" :sources="sourceMap" @cite="cite" /></th></tr></thead><tbody><tr v-for="(row, ri) in block.rows" :key="ri"><td v-for="(cell, ci) in row" :key="ci"><InlineText :text="cell" :tokens="tokens" :cites="cites" :sources="sourceMap" @cite="cite" /></td></tr></tbody></table></div>
          <p v-else :class="{ clause: block.clause, heading: block.kind === 'heading', list: block.kind === 'list' }" :style="block.kind === 'list' && block.level ? { marginLeft: `${block.level * 20}px` } : undefined"><span v-if="block.kind === 'list'">{{ block.order ? `${block.order}. ` : '• ' }}</span><InlineText :text="block.text" :tokens="tokens" :cites="cites" :sources="sourceMap" @cite="cite" /><i v-if="streaming && index === parsed.length - 1" class="cursor" aria-hidden="true" /></p>
        </template>
        <p v-if="streaming && parsed[parsed.length - 1]?.kind !== 'paragraph' && parsed[parsed.length - 1]?.kind !== 'heading' && parsed[parsed.length - 1]?.kind !== 'list'"><i class="cursor" aria-hidden="true" /></p>
      </div>
      <div v-if="receipt" class="receipt" :class="{ open: receiptOpen }">
        <button type="button" class="rh" :aria-expanded="receiptOpen" @click="receiptOpen = !receiptOpen">
          <Send :size="14" :stroke-width="1.75" aria-hidden="true" /><b>查看实际发送内容</b>
          <template v-if="codeCount"><i class="sp" />遮住 <span class="num">{{ codeCount }}</span> 处</template>
          <i class="sp" /><span class="pass"><ShieldCheck :size="13" :stroke-width="2" aria-hidden="true" />复检通过</span>
          <template v-if="message.model_label"><i class="sp" />{{ message.model_label }}</template>
          <ChevronDown :size="16" :stroke-width="1.75" class="chev" aria-hidden="true" />
        </button>
        <div v-if="receiptOpen" class="rb">
          <p class="rb-h">你的提问 · 发出时的样子</p>
          <pre>{{ sent }}</pre>
          <p v-if="codeCount" class="rb-note">文件正文按确认后的遮住版本一并发出，可在“文件”里点“查看外发文本”。</p>
        </div>
      </div>
    </component>
    <span v-if="message.role === 'user'" class="aux num">{{ formatDateTime(message.created_at) }}</span>
    <p v-if="waiting" class="waiting" :class="reading ? 'is-local' : 'is-ai'" role="status"><span v-if="!reading" class="think" aria-hidden="true"><i /><i /><i /></span><span>{{ waiting }}{{ waitingTail }}</span></p>
    <p v-if="count" class="warning">有 {{ count }} 处没能还原为真实名称，已用虚线标出，原文保持不变</p>
    <SiNotice v-if="message.error" :title="message.error" type="error" :closable="false" />
    <SiNotice v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="sources.length" class="sources" role="group" aria-label="回复依据">
      <span class="hint">依据</span>
      <button v-for="source in sources" :key="source.ref" type="button" class="source" :class="{ uncited: !source.cited }" :title="source.cited ? '' : '已附带发送，回复中未引用'" @click="cite(source, $event.currentTarget as HTMLElement)"><span class="source-kind">{{ kindLabel[source.kind] }}{{ source.kind === 'kb' ? ' ' + source.ref.slice(2) : '' }}</span>{{ source.kind === 'kb' ? source.label.replace(/^[^·]+· /, '') : source.label }}</button>
    </div>
    <p v-if="message.role === 'assistant' && Number(message.extras.kb_dropped) > 0" class="hint">另有 {{ message.extras.kb_dropped }} 段知识库片段因含疑似名称或金额未发送</p>
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
.message { display: flex; flex-direction: column; gap: 8px; padding: 14px 0; }
/* 用户消息：靠右，次级卡片底；模型回答：靠左，不加气泡，阅读字号 15px（澄明 8.3） */
.mine { align-items: flex-end; }
.bubble { max-width: 80%; box-sizing: border-box; padding: 12px 16px; border: 1px solid var(--si-hair); border-radius: 16px 16px 4px 16px; background: var(--si-card-2); }
.bubble .markdown p:first-child { margin-top: 0; }
.bubble .markdown p:last-child { margin-bottom: 0; }
.aux { color: var(--si-ink-4); font: var(--si-font-caption); }
.ai-line { display: flex; align-items: center; gap: 10px; }
.ai-tag, .local-tag { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: var(--si-radius-badge); font: var(--si-font-badge); }
.ai-tag { background: var(--si-ai-soft); color: var(--si-ai-ink); }
.ai-tag svg { color: var(--si-ai); }
.local-tag { background: var(--si-blue-tint); color: var(--si-blue-ink); }
/* 回答放在紫色地带（二版 6.5），标题“AI 建议 · 请核实”；本机给出的回答不加地带 */
.ai-zone .markdown > p:first-child { margin-top: 0; }
.ai-zone .markdown > p:last-child { margin-bottom: 0; }
/* 外发回执：虚线边，合上一行；翻开是实际发出的提问文字，代号原样显示 */
.receipt { margin-top: 14px; overflow: hidden; border: 1px dashed var(--si-ai-line); border-radius: var(--si-radius-md); background: var(--si-surface-1); }
.rh { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; width: 100%; padding: 8px 12px; border: 0; background: none; color: var(--si-ink-3); font: var(--si-font-meta); text-align: left; cursor: pointer; }
.rh:hover { color: var(--si-ink-2); }
.rh b { color: var(--si-ink-2); font-weight: 500; }
.rh .sp { width: 3px; height: 3px; border-radius: 50%; background: var(--si-ink-4); }
.rh .pass { display: inline-flex; align-items: center; gap: 4px; height: 20px; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-badge); }
.rh .chev { margin-left: auto; transition: transform var(--si-d2) var(--si-ease-out); }
.receipt.open .chev { transform: rotate(180deg); }
.rb { padding: 12px 14px; border-top: 1px dashed var(--si-ai-line); background: var(--si-card-2); white-space: normal; }
.rb-h { margin: 0 0 6px; font: var(--si-font-caption); color: var(--si-ink-3); }
.rb pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-code-sm); color: var(--si-ink-2); }
.rb-note { margin: 10px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
.markdown { font: var(--si-font-reading); color: var(--si-ink-1); overflow-wrap: anywhere; white-space: pre-wrap; }
.markdown p { margin: 10px 0; }
.heading { font: var(--si-font-h3); }
.clause { padding: 12px 16px; border-left: 2px solid var(--si-brand-line); background: var(--si-surface-2); border-radius: var(--si-radius-paper); }
strong { font-weight: 600; }
.list { padding-left: 16px; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font: var(--si-font-body); }
td, th { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--si-hair); }
th { background: var(--si-surface-2); }
.warning { margin: 0; color: var(--si-warn-ink); font: var(--si-font-meta); }
.code { margin: 12px 0; padding: 10px 14px; overflow-x: auto; white-space: pre; font: var(--si-font-code); background: var(--si-code-bg); color: var(--si-code-ink); border-radius: var(--el-border-radius-base); }
.cursor { display: inline-block; width: 2px; height: 1.1em; margin-left: 2px; vertical-align: -3px; background: var(--si-ai); animation: cursor-blink 1s steps(2) infinite; }
@keyframes cursor-blink { 50% { opacity: 0; } }
.waiting { display: flex; align-items: center; gap: 10px; margin: 0; font: var(--si-font-body); }
.waiting.is-local { color: var(--si-blue-ink); }
.waiting.is-ai { color: var(--si-ai-ink); }
.think { display: inline-flex; align-items: center; gap: 4px; }
.think i { width: 6px; height: 6px; border-radius: 50%; background: var(--si-ai); animation: think-dot 1.2s var(--si-ease-io) infinite; }
.think i:nth-child(2) { animation-delay: .15s; }
.think i:nth-child(3) { animation-delay: .3s; }
@keyframes think-dot { 0%, 100% { opacity: .3; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
html[data-fx="lite"] .think i { animation: none; opacity: .7; }
.message-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.draft-action { display: flex; align-items: center; gap: 8px; }
.draft-saved { color: var(--si-ok-ink); font: var(--si-font-label-sm); }
.message-actions :deep(.el-button) { margin-left: 0; }
/* 悬停该条回复或键盘聚焦其中时显示操作；保持占位，不跳动 */
.message-actions.on-hover { opacity: 0; transition: opacity var(--si-d2) var(--si-ease-out); }
.reply:hover .message-actions.on-hover, .reply:focus-within .message-actions.on-hover { opacity: 1; }
/* 依据行（V321-E-13）：引用过的在前；附带发送但未引用的用次要色 */
.sources { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.source { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 2px 10px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-badge); background: var(--si-surface-1); font: var(--si-font-meta); color: var(--si-ink-2); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; transition: border-color var(--si-d2), color var(--si-d2); }
.source:hover { border-color: var(--si-brand-line); color: var(--si-blue-ink); }
.source:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.source.uncited { color: var(--si-ink-3); border-style: dashed; }
.source-kind { font: var(--si-font-caption); color: var(--si-blue-ink); }
.uncited .source-kind { color: var(--si-ink-3); }
</style>
