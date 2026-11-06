<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Files } from '@lucide/vue'
import { assistantApi, type Session, type SessionItem, type Message, type Template } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { useSessionsStore } from '@/stores/sessions'
import Conversation from '@/components/assistant/Conversation.vue'
import Composer, { type ComposerStart } from '@/components/assistant/Composer.vue'
import FilePanel from '@/components/assistant/FilePanel.vue'
const route = useRoute(), router = useRouter(), sessions = useSessionsStore()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), template = ref<Template | null>(null)
const loading = ref(false), error = ref(''), working = ref(false)
const offsets = new Map<number, number>()
let active = true, version = 0, polling = false, timer: ReturnType<typeof setTimeout> | undefined
function failure(e: unknown) { return e instanceof ApiError ? e.status === 404 ? '会话不存在或已被删除' : e.message : '加载失败，请刷新页面后重试' }
function schedule() {
  clearTimeout(timer)
  // 回复生成中每秒取增量；只有解析 / 转写时才放慢到 3 秒
  const streaming = messages.value.some(m => ['queued', 'streaming'].includes(m.status))
  if (!document.hidden && active && session.value && (session.value.status === 'parsing' || streaming)) timer = setTimeout(poll, streaming ? 1000 : 3000)
}
async function load() {
  const current = ++version, selected = id.value
  clearTimeout(timer); session.value = null; messages.value = []; offsets.clear(); drawerOpen.value = false
  template.value = carryTemplate; carryTemplate = null
  loading.value = !!selected; error.value = ''
  if (!selected || !active || current !== version) { loading.value = false; return }
  try {
    const [detail, response] = await Promise.all([assistantApi.get(selected), assistantApi.messages(selected)])
    if (!active || current !== version) return
    session.value = detail
    messages.value = response.items.map(item => ['queued', 'streaming'].includes(item.status) ? { ...item, content: '' } : item)
    void nextTick(() => { initial.value = null })
  } catch (e) { if (active && current === version) error.value = failure(e) }
  finally { if (active && current === version) { loading.value = false; schedule() } }
}
async function poll() {
  if (!active || !session.value || polling) { schedule(); return }
  polling = true
  const current = version, selected = id.value
  try {
    let finished = false
    for (const item of messages.value.filter(m => ['queued', 'streaming'].includes(m.status))) {
      const delta = await assistantApi.delta(selected, item.id, offsets.get(item.id) ?? 0)
      if (!active || current !== version) return
      item.content += delta.text_from_offset; item.status = delta.status; item.error = delta.error
      if (delta.compare) item.compare = delta.compare
      offsets.set(item.id, delta.offset)
      if (item.template === 'translate' && ['queued', 'streaming'].includes(delta.status)) {
        // 只补读当前译文的结构化进度，不重复拉取整段会话历史；正文仍按增量偏移追加。
        const fresh = (await assistantApi.messages(selected, item.id - 1)).items.find(row => row.id === item.id)
        if (!active || current !== version) return
        if (fresh) { item.extras = fresh.extras; item.translation = fresh.translation; item.translation_notice = fresh.translation_notice }
      }
      if (!['queued', 'streaming'].includes(delta.status)) finished = true
    }
    if (finished) {
      const response = await assistantApi.messages(selected)
      if (!active || current !== version) return
      messages.value = response.items
    }
    const detail = await assistantApi.get(selected)
    if (!active || current !== version) return
    session.value = detail; error.value = ''
    // 生成结束后刷新侧栏，去掉“生成中”圆点
    if (finished) void sessions.refresh()
  } catch (e) {
    if (active && current === version) {
      error.value = failure(e)
      if (e instanceof ApiError && e.status === 404) { session.value = null; return }
    }
  } finally { polling = false; if (active && current === version) schedule() }
}
async function refresh() {
  const selected = id.value, current = version
  try {
    const [detail, response] = await Promise.all([assistantApi.get(selected), assistantApi.messages(selected)])
    if (!active || current !== version) return
    session.value = detail; messages.value = response.items.map(item => {
      const previous = messages.value.find(old => old.id === item.id)
      return ['queued', 'streaming'].includes(item.status) ? { ...item, content: previous?.content ?? '' } : item
    })
    error.value = ''; void sessions.refresh(); schedule()
  } catch (e) { if (active && current === version) error.value = failure(e) }
}
// 首页：第一次发送、加入文件或选模板时才建会话（不再先建空会话，V31-U5 2.1）
const initial = ref<{ text: string; autoSend: boolean; modelId?: number } | null>(null), homeUpload = ref<number | null>(null)
let carryTemplate: Template | null = null
async function start(payload: ComposerStart) {
  if (working.value) return
  working.value = true; error.value = ''
  try {
    const created = await assistantApi.create()
    if (payload.files.length) {
      homeUpload.value = 0
      try { await assistantApi.addFiles(created.id, payload.files, (loaded, total) => { homeUpload.value = Math.round(loaded / total * 100) }) }
      finally { homeUpload.value = null }
    }
    carryTemplate = payload.template
    initial.value = { text: payload.text, modelId: payload.modelId, autoSend: !!payload.text.trim() || (!!payload.template && payload.files.length > 0) }
    await router.push(`/assistant/${created.id}`)
    void sessions.refresh()
  } catch (e) { error.value = failure(e) }
  finally { working.value = false }
}
// 第一句问题作为会话标题（P4）
async function sent(text: string) {
  await refresh()
  if (session.value && session.value.title === '新会话' && text.trim()) {
    try { session.value = await assistantApi.patch(id.value, { title: text.trim().replace(/\s+/g, ' ').slice(0, 20) }); void sessions.refresh() } catch { /* 标题不影响使用 */ }
  }
}
const filePanel = ref<InstanceType<typeof FilePanel>>(), drawerOpen = ref(false)
const uploading = computed(() => homeUpload.value ?? filePanel.value?.uploadProgress ?? null)
function openFile(file: { id: string; media: unknown; confirmed_at: string | null }) {
  const target = session.value?.files.find(f => f.id === file.id)
  if (!target) return
  if (target.media) filePanel.value?.openTranscript(target); else filePanel.value?.openConfirm()
}
function addFiles(files: File[]) { void filePanel.value?.addList(files) }
async function remove(item: SessionItem) {
  try { await ElMessageBox.confirm(`将删除“${item.title}”及其文件、消息与对照表，无法恢复。`, '删除会话', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try { await assistantApi.remove(item.id); if (item.id === id.value) await router.push('/assistant'); void sessions.refresh() }
  catch (e) { error.value = failure(e) }
}
async function rename() {
  if (!session.value) return
  try {
    const { value } = await ElMessageBox.prompt('输入会话标题，最多 60 字', '修改会话标题', { inputValue: session.value.title, inputValidator: value => !!value.trim() && value.trim().length <= 60 || '请输入 1～60 字标题', confirmButtonText: '保存', cancelButtonText: '取消' })
    session.value = await assistantApi.patch(id.value, { title: value }); void sessions.refresh()
  } catch (e) { if (e instanceof ApiError) error.value = e.message }
}
async function stop(mid: number) { try { await assistantApi.stop(id.value, mid); await refresh() } catch (e) { error.value = failure(e) } }
async function retry(mid: number) { try { await assistantApi.retry(id.value, mid); await refresh() } catch (e) { error.value = failure(e) } }
// 本会话正在生成的回复（输入框的发送按钮原位变为停止）
// V31-U5F-04：只加了文件、还没发出第一条消息时保持首页布局（输入框不动，文件标签在框内），发出后才转为对话布局
const homeLike = computed(() => !!session.value && !messages.value.length)
const generating = computed(() => messages.value.find(m => m.role === 'assistant' && ['queued', 'streaming'].includes(m.status))?.id ?? null)
// 在侧栏改名后同步标题
watch(() => sessions.items.find(item => item.id === id.value)?.title, title => { if (title && session.value) session.value.title = title })
watch(id, load, { immediate: true })
function visibility() { if (document.hidden) clearTimeout(timer); else void poll() }
onMounted(() => document.addEventListener('visibilitychange', visibility))
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', visibility); active = false; ++version; clearTimeout(timer); offsets.clear() })
</script>
<template>
  <div class="page page--split assistant-page">
    <main v-loading="loading" class="dialogue" :class="{ 'is-home': homeLike || (!session && !id) }">
      <el-alert v-if="error" type="error" :closable="false" :title="error" />
      <template v-if="session">
        <h1 v-if="homeLike" class="home-title">今天要处理什么？</h1>
        <div v-else class="toolbar">
          <el-button class="title" link @click="rename">{{ session.title }}</el-button>
          <el-button size="small" @click="drawerOpen = true"><Files :size="14" :stroke-width="1.75" /><span class="files-label">文件 {{ session.files.length }}</span></el-button>
          <el-button link type="danger" :disabled="working" @click="remove(session)">删除会话</el-button>
        </div>
        <Transition name="rise"><Conversation v-if="!homeLike" :messages="messages" :session-id="id" @stop="stop" @retry="retry" /></Transition>
        <Composer :key="id" v-model:template="template" :session="session" :generating="generating" :asr-enabled="session.asr_enabled" :show-templates="!messages.length" :uploading="uploading" :initial="initial" @sent="sent" @stop="stop" @files="addFiles" @open="openFile" @remove="filePanel?.remove($event)" @confirm="filePanel?.openConfirm()" @retry="filePanel?.retryMedia($event)" />
      </template>
      <div v-else-if="!loading && !id" class="home">
        <h1>今天要处理什么？</h1>
        <Composer v-model:template="template" :session="null" :generating="null" :asr-enabled="sessions.asrEnabled" show-templates :uploading="homeUpload" @start="start" />
      </div>
    </main>
    <FilePanel v-if="session" ref="filePanel" :key="id" v-model:open="drawerOpen" :session="session" @refresh="refresh" />
  </div>
</template>
<style scoped>
.assistant-page { height: 100%; box-sizing: border-box; padding-top: 20px; padding-bottom: 20px; overflow: hidden; }
.dialogue { height: 100%; min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--el-border-color-light); }
.title { flex: 1; min-width: 0; justify-content: flex-start; white-space: normal; overflow-wrap: anywhere; text-align: left; font: var(--si-font-h2); }
.files-label { margin-left: 6px; }
.home { flex: 1; display: flex; flex-direction: column; justify-content: center; padding-bottom: 8vh; }
.home h1, .home-title { margin: 0 0 20px; text-align: center; font: var(--si-font-title-lg); color: var(--si-ink-1); }
/* 首页布局：标题与输入框居中，位置与未建会话时相同 */
.dialogue.is-home { justify-content: center; padding-bottom: 8vh; }
.rise-enter-active { transition: opacity var(--si-dur-2, 240ms) var(--si-ease), transform var(--si-dur-2, 240ms) var(--si-ease); }
.rise-enter-from { opacity: 0; transform: translateY(12px); }
@media (prefers-reduced-motion: reduce) { .rise-enter-active { transition: none; } }
</style>
