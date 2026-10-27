<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound } from '@element-plus/icons-vue'
import { chatApi, type ChatMessage, type ChatPreview, type ChatDecision } from '@/api/chat'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'
import { markPending, type CompareSeg } from '@/utils/compare'
import { USER_CATEGORY_OPTIONS, suspectReasonLabel, toOptions } from '@/utils/labels'
import type { UserCategory } from '@/api/redaction'

// Q-98：挂载和打开抽屉时读取可用性；加载失败禁用入口并提示刷新，轮次切换共用任务对话。
const props = defineProps<{ taskId: string; disabled?: boolean }>()
const open = ref(false)
const loading = ref(true)
const available = ref(false)
const reason = ref('正在加载追问状态，请稍候')
const error = ref('')
const items = ref<ChatMessage[]>([])
const message = ref('')
const busy = ref(false)
const sending = ref(false)
const confirmOpen = ref(false)
const preview = ref<ChatPreview | null>(null)
const confirmation = ref<ChatPreview | null>(null)
const decisions = ref<Array<{ choice: '' | 'replace' | 'keep'; category: UserCategory | '' }>>([])
const skipConfirm = ref(false)
const messageList = ref<HTMLElement>()
let pendingMessage = ''
let active = true
let previewVersion = 0
let previewTimer: ReturnType<typeof setTimeout> | undefined
const categoryOptions = toOptions(USER_CATEGORY_OPTIONS)
const allDecided = computed(() => decisions.value.every(d => d.choice === 'keep' || (d.choice === 'replace' && !!d.category)))

// Q-160：预览没有代号位置表，只按既有格式着色；文字始终来自服务端，不在前端生成或替换代号。
function segments(data: ChatPreview): CompareSeg[] {
  const parts = data.redacted.split(/(\[(?:[^\[\]\n]+_Q\d+(?:：[^\[\]\n]*)?|账号_\d+|SWIFT_\d+|金额_\d+|单价_\d+|数量_\d+|敏感词_\d+|电话|邮箱|网址|证件号|邮编|地址：[^\[\]\n]+)\]|(?:客户|供应商|买方|卖方|公司|收货人|通知方|受益人|银行)_[A-Z]+|人员_\d+|编号_\d+|我方)/g)
  return markPending(parts.map((text, i) => ({ text, placeholder: i % 2 ? text : undefined })).filter(seg => !!seg.text),
    data.suspects.map(s => ({ text: s.text, decision: 'pending' })))
}

function contextFor(start: number, end: number) {
  const chars = Array.from(pendingMessage)
  return { before: chars.slice(Math.max(0, start - 20), start).join(''), after: chars.slice(end, end + 20).join('') }
}

watch(message, () => {
  const current = ++previewVersion
  clearTimeout(previewTimer)
  preview.value = null
  const text = message.value.trim()
  if (!text || !open.value) return
  previewTimer = setTimeout(async () => {
    try {
      const result = await chatApi.preview(props.taskId, text)
      if (active && current === previewVersion && open.value) preview.value = result
    } catch { /* 常驻预览失败时隐藏；发送仍由后端重新脱敏并校验。 */ }
  }, 600)
})
watch(open, value => {
  if (!value) { ++previewVersion; clearTimeout(previewTimer); preview.value = null }
})

function askConfirm(data: ChatPreview) {
  confirmation.value = data
  decisions.value = data.suspects.map(s => ({ choice: '', category: s.reason === 'person_name' ? 'PERSON' : '' }))
  skipConfirm.value = false
  confirmOpen.value = true
}

const buttonReason = computed(() => props.disabled ? '正在删除任务，请稍候' : reason.value)
const canSend = computed(() => available.value && !loading.value && !error.value && !busy.value
  && !props.disabled && !!message.value.trim())
const preferenceKey = computed(() => `chat-skip-confirm:${props.taskId}`)

function readPreference() {
  try { return localStorage.getItem(preferenceKey.value) === 'true' }
  catch { return false }
}

function errorMessage(e: unknown) {
  return e instanceof ApiError
    ? e.status === 404 ? '任务不存在或已被删除' : e.message
    : '追问失败，请稍后重试'
}

async function scrollToLatest() {
  await nextTick()
  if (active && messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await chatApi.list(props.taskId)
    if (!active) return
    items.value = data.items
    available.value = data.available
    reason.value = data.available ? '' : data.reason || '当前任务暂时不能追问'
    await scrollToLatest()
  } catch (e) {
    if (!active) return
    error.value = `${errorMessage(e)}，请刷新页面后重试`
    available.value = false
    reason.value = error.value
  } finally { if (active) loading.value = false }
}

async function show() {
  open.value = true
  await load()
  if (available.value && !error.value && message.value.trim()) {
    const text = message.value.trim()
    const current = ++previewVersion
    try {
      const data = await chatApi.preview(props.taskId, text)
      if (active && current === previewVersion && open.value) preview.value = data
    } catch { /* 预览失败不阻止发送。 */ }
  }
}

function cancelConfirm() {
  if (sending.value) return
  confirmOpen.value = false
  pendingMessage = ''
  confirmation.value = null
  decisions.value = []
  busy.value = false
}

async function submit() {
  if (!canSend.value) return
  busy.value = true
  pendingMessage = message.value.trim()
  clearTimeout(previewTimer)
  ++previewVersion
  try {
    // 每次发送都先请求服务端预览；发送仍提交原问题，由服务端重新脱敏。
    let data: ChatPreview | null = null
    try { data = await chatApi.preview(props.taskId, pendingMessage) }
    catch { /* 预览失败不阻止发送；后端仍强制校验疑似遗漏。 */ }
    if (!active) return
    preview.value = data
    if (data && (data.suspects.length || (data.changed && !readPreference()))) {
      askConfirm(data)
      return
    }
    await send()
  } catch (e) { await recoverFailure(e) }
  finally { if (active && !confirmOpen.value) busy.value = false }
}

async function recoverFailure(e: unknown) {
  if (!active) return
  if (e instanceof ApiError && e.code === 'CHAT_SUSPECTS_PENDING') {
    try {
      const data = await chatApi.preview(props.taskId, pendingMessage)
      if (!active) return
      preview.value = data
      askConfirm(data)
      return
    } catch (refreshError) { handleFailure(refreshError); return }
  }
  handleFailure(e)
}

function handleFailure(e: unknown) {
  if (!active) return
  ElMessage.error(errorMessage(e))
  if (e instanceof ApiError && (e.code === 'CHAT_UNAVAILABLE' || e.status === 404)) {
    available.value = false
    reason.value = errorMessage(e)
  }
}

async function send(confirmedDecisions: ChatDecision[] = []) {
  sending.value = true
  try {
    const reply = await chatApi.send(props.taskId, pendingMessage, confirmedDecisions)
    if (!active) return
    items.value.push(reply.user, reply.assistant)
    message.value = ''
    pendingMessage = ''
    await scrollToLatest()
  } finally { if (active) sending.value = false }
}

async function confirmSend() {
  if (sending.value || !confirmOpen.value || !allDecided.value) return
  // Q-98：偏好只在确认发送后生效；存储失败视作未勾选，下次仍提示。
  if (!confirmation.value?.suspects.length) {
    try { localStorage.setItem(preferenceKey.value, String(skipConfirm.value)) }
    catch { skipConfirm.value = false }
  }
  const selected: ChatDecision[] = (confirmation.value?.suspects ?? []).map((s, index) => {
    const d = decisions.value[index]!
    return { start: s.start, end: s.end, decision: d.choice as 'replace' | 'keep',
      ...(d.choice === 'replace' ? { category: d.category as UserCategory } : {}) }
  })
  confirmOpen.value = false
  try { await send(selected) }
  catch (e) { await recoverFailure(e) }
  finally { if (active && !confirmOpen.value) busy.value = false }
}

onMounted(load)
onBeforeUnmount(() => { active = false; ++previewVersion; clearTimeout(previewTimer) })
</script>

<template>
  <el-tooltip :content="buttonReason" :disabled="!buttonReason" placement="top">
    <span class="chat-trigger">
      <el-button :icon="ChatDotRound" :disabled="!available || loading || !!error || disabled" @click="show">追问</el-button>
    </span>
  </el-tooltip>

  <el-drawer class="u7-chatpanel" v-model="open" title="追问" size="480px" direction="rtl" append-to-body
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy"
    @opened="scrollToLatest">
    <div class="chat-panel">
      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
      <el-alert v-else-if="!loading && !available" type="warning" :closable="false" show-icon :title="reason" />
      <div ref="messageList" v-loading="loading" class="messages" aria-live="polite">
        <el-empty v-if="!loading && !error && !items.length" description="暂无对话记录" />
        <div v-for="item in items" :key="item.id" class="message" :class="{ 'message--user': item.role === 'user' }">
          <div class="hint">{{ item.role === 'user' ? '我' : '回复' }} · {{ formatDateTime(item.created_at) }}</div>
          <div class="message-content">{{ item.content }}</div>
        </div>
      </div>
      <el-input v-model="message" type="textarea" :rows="4" :maxlength="1000" show-word-limit
        aria-label="追问问题" placeholder="输入要追问的问题，Ctrl+Enter 发送"
        :disabled="loading || !available || !!error || busy || disabled"
        @keydown.ctrl.enter.prevent="submit" />
      <div v-if="preview && message.trim() && available && !error" class="live-preview" aria-live="polite">
        <p class="hint">将发送：</p>
        <pre class="redacted"><span v-for="(seg, index) in segments(preview)" :key="index" :class="{ placeholder: seg.placeholder, suspect: seg.suspect }">{{ seg.text }}</span></pre>
      </div>
      <p v-if="sending" class="hint">正在等待回复，最长约 2 分钟</p>
      <div class="actions">
        <el-button type="primary" :loading="busy" :disabled="!canSend" @click="submit">发送</el-button>
      </div>
    </div>
  </el-drawer>

  <el-dialog class="u7-chatpanel" :model-value="confirmOpen" title="确认发送内容" width="640px" top="24px" append-to-body
    :close-on-click-modal="false" @update:model-value="cancelConfirm">
    <div class="chat-confirm-body">
    <p>将发送：</p>
    <pre v-if="confirmation" class="redacted"><span v-for="(seg, index) in segments(confirmation)" :key="index" :class="{ placeholder: seg.placeholder, suspect: seg.suspect }">{{ seg.text }}</span></pre>
    <div v-if="confirmation?.suspects.length" class="suspect-decisions">
      <p class="hint">请逐条选择替换或保留。选择替换时，发送前由服务端分配代号。</p>
      <div v-for="(s, index) in confirmation.suspects" :key="`${s.start}:${s.end}`" class="suspect-decision">
        <p class="hint">{{ suspectReasonLabel(s.reason) }}</p>
        <p class="context">{{ contextFor(s.start, s.end).before }}<mark class="suspect">{{ s.text }}</mark>{{ contextFor(s.start, s.end).after }}</p>
        <el-radio-group v-model="decisions[index]!.choice" :aria-label="`第 ${index + 1} 条疑似遗漏的处理方式`">
          <el-radio value="replace">替换</el-radio><el-radio value="keep">保留</el-radio>
        </el-radio-group>
        <el-select v-if="decisions[index]!.choice === 'replace'" v-model="decisions[index]!.category" placeholder="请选择替换类别" :aria-label="`第 ${index + 1} 条替换类别`">
          <el-option v-for="option in categoryOptions" :key="option.value" :value="option.value" :label="option.label" />
        </el-select>
      </div>
    </div>
    <el-checkbox v-else v-model="skipConfirm">本任务内不再提示</el-checkbox>
    </div>
    <template #footer>
      <el-button @click="cancelConfirm">取消</el-button>
      <el-button type="primary" :disabled="!allDecided" @click="confirmSend">确认发送</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.u7-chatpanel .el-dialog__title), :global(.u7-chatpanel .el-drawer__title) { font: var(--si-font-h2); }
:global(.u7-chatpanel .el-dialog__body), :global(.u7-chatpanel .el-drawer__body) { font: var(--si-font-body); }

.chat-trigger { display: inline-block; }
.chat-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.live-preview { flex-shrink: 0; max-height: 200px; overflow-y: auto; margin-top: 12px; }
.live-preview .hint { margin: 0 0 4px; }
.placeholder { color: var(--el-color-primary); background: var(--el-color-primary-light-9); border-radius: 6px; }
.suspect { color: inherit; background: var(--el-color-warning-light-9); border-bottom: 2px solid var(--el-color-warning); }
.suspect-decisions { max-height: 240px; overflow-y: auto; }
.suspect-decision { padding: 12px 0; border-top: 1px solid var(--el-border-color-light); }
.suspect-decision :deep(.el-select) { width: 160px; margin-left: 12px; }
.context { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
.chat-confirm-body { max-height: calc(100vh - 200px); overflow-y: auto; }
.messages { flex: 1; min-height: 120px; overflow-y: auto; margin-bottom: 16px; padding: 12px; background: var(--el-fill-color-light); border-radius: 12px; }
.message { max-width: 90%; margin-bottom: 16px; }
.message--user { margin-left: auto; text-align: right; }
.message-content { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 4px; padding: 12px 16px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); text-align: left; font: var(--si-font-body); }
.message--user .message-content { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-8); }
.redacted {
  margin: 0 0 12px;
  padding: 12px;
  max-height: 320px;
  overflow-y: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
  background: var(--el-fill-color-light);
}
</style>
