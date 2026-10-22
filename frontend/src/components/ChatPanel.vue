<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { chatApi, type ChatMessage } from '@/api/chat'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'

// 由结果页按 task.id 挂载，轮次切换共用同一个任务对话。
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
const redacted = ref('')
const skipConfirm = ref(false)
const messageList = ref<HTMLElement>()
let pendingMessage = ''
let active = true

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
}

function cancelConfirm() {
  if (sending.value) return
  confirmOpen.value = false
  pendingMessage = ''
  busy.value = false
}

async function submit() {
  if (!canSend.value) return
  busy.value = true
  pendingMessage = message.value.trim()
  try {
    // 每次发送都先请求服务端预览；发送仍提交原问题，由服务端重新脱敏。
    const preview = await chatApi.preview(props.taskId, pendingMessage)
    if (!active) return
    if (preview.changed && !readPreference()) {
      redacted.value = preview.redacted
      skipConfirm.value = false
      confirmOpen.value = true
      return
    }
    await send()
  } catch (e) { handleFailure(e) }
  finally { if (active && !confirmOpen.value) busy.value = false }
}

function handleFailure(e: unknown) {
  if (!active) return
  ElMessage.error(errorMessage(e))
  if (e instanceof ApiError && (e.code === 'CHAT_UNAVAILABLE' || e.status === 404)) {
    available.value = false
    reason.value = errorMessage(e)
  }
}

async function send() {
  sending.value = true
  try {
    const reply = await chatApi.send(props.taskId, pendingMessage)
    if (!active) return
    items.value.push(reply.user, reply.assistant)
    message.value = ''
    pendingMessage = ''
    await scrollToLatest()
  } finally { if (active) sending.value = false }
}

async function confirmSend() {
  if (sending.value || !confirmOpen.value) return
  // 偏好只在确认发送后生效；存储失败视作未勾选，下次仍提示。
  try { localStorage.setItem(preferenceKey.value, String(skipConfirm.value)) }
  catch { skipConfirm.value = false }
  confirmOpen.value = false
  try { await send() }
  catch (e) { handleFailure(e) }
  finally { if (active) busy.value = false }
}

onMounted(load)
onBeforeUnmount(() => { active = false })
</script>

<template>
  <el-tooltip :content="buttonReason" :disabled="!buttonReason" placement="top">
    <span class="chat-trigger">
      <el-button :disabled="!available || loading || !!error || disabled" @click="show">追问</el-button>
    </span>
  </el-tooltip>

  <el-drawer v-model="open" title="追问" size="480px" direction="rtl" append-to-body
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
      <p v-if="sending" class="hint">正在等待回复，最长约 2 分钟</p>
      <div class="actions">
        <el-button type="primary" :loading="busy" :disabled="!canSend" @click="submit">发送</el-button>
      </div>
    </div>
  </el-drawer>

  <el-dialog :model-value="confirmOpen" title="确认发送内容" width="640px" append-to-body
    :close-on-click-modal="false" @update:model-value="cancelConfirm">
    <p>将发送：</p>
    <pre class="redacted">{{ redacted }}</pre>
    <el-checkbox v-model="skipConfirm">本任务内不再提示</el-checkbox>
    <template #footer>
      <el-button @click="cancelConfirm">取消</el-button>
      <el-button type="primary" @click="confirmSend">确认发送</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.chat-trigger { display: inline-block; margin-right: 12px; }
.chat-panel { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.messages { flex: 1; min-height: 120px; overflow-y: auto; margin-bottom: 16px; }
.message { max-width: 90%; margin-bottom: 16px; }
.message--user { margin-left: auto; text-align: right; }
.message-content { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 4px; }
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
