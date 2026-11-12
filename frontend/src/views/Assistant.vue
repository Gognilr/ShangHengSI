<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Files, Upload, X } from '@lucide/vue'
import { assistantApi, type Session, type SessionItem, type Message, type Template } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { useSessionsStore } from '@/stores/sessions'
import { usePasteFiles } from '@/composables/usePasteFiles'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import ReviewPane from '@/components/workspace/ReviewPane.vue'
import Conversation from '@/components/assistant/Conversation.vue'
import Composer, { type ComposerStart } from '@/components/assistant/Composer.vue'
import FilePanel from '@/components/assistant/FilePanel.vue'
import DraftDialog from '@/components/workspace/DraftDialog.vue'
const route = useRoute(), router = useRouter(), sessions = useSessionsStore(), auth = useAuthStore()
// 工作台两种视图（V32-B-01）：直接提问 / 全面审核。切换只改视图，不外发、不清空输入；没有“智能审核”权限的人看不到审核视图
const canReview = computed(() => auth.can('use.review'))
const mode = computed<'ask' | 'review'>(() => canReview.value && (route.query.mode === 'review' || !auth.can('use.assistant')) ? 'review' : 'ask')
const modeModel = computed<'ask' | 'review'>({
  get: () => mode.value,
  set: value => { void router.replace({ path: route.path, query: value === 'review' ? { mode: 'review' } : {} }) },
})
const modeOptions = [{ value: 'ask' as const, label: '直接提问' }, { value: 'review' as const, label: '全面审核' }]
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), template = ref<Template | null>(null)
const loading = ref(false), error = ref(''), working = ref(false)
// 按问题讨论（V32-B-02）：锚点只存在于本页，切换会话即清除；有锚点时只显示该问题下的对话，普通对话与各问题互不混入
interface Anchor { id: string; no: string; title: string; runNo: number }
const anchor = ref<Anchor | null>(null)
const shown = computed(() => messages.value.filter(m => anchor.value ? m.finding_id === anchor.value.id : !m.finding_id))
function discuss(next: Anchor) { anchor.value = next; modeModel.value = 'ask' }
// 讨论回复保存为该问题的修改建议（V32-B-03）；回到审核结果时问题卡重新读取，显示二选一
const draftOpen = ref(false), draftMessage = ref<Message | null>(null)
function openDraft(message: Message) { draftMessage.value = message; draftOpen.value = true }
const offsets = new Map<number, number>()
let active = true, version = 0, polling = false, timer: ReturnType<typeof setTimeout> | undefined
function failure(e: unknown) { return e instanceof ApiError ? e.status === 404 ? '会话不存在或已被删除' : e.message : '加载失败，请刷新页面后重试' }
function schedule() {
  clearTimeout(timer)
  // 回复生成中每秒取增量；只有解析 / 转写时才放慢到 3 秒
  const streaming = messages.value.some(m => ['queued', 'streaming'].includes(m.status))
  const reviewing = !!session.value?.review_running
  if (!document.hidden && active && session.value && (session.value.status === 'parsing' || streaming || reviewing)) timer = setTimeout(poll, streaming ? 1000 : 3000)
}
async function load() {
  const current = ++version, selected = id.value
  clearTimeout(timer); session.value = null; messages.value = []; offsets.clear(); drawerOpen.value = false; anchor.value = null
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
  if (payload.files.some(f => /\.(mp3|m4a|wav|amr|aac|mp4)$/i.test(f.name))) { error.value = '录音请到“会议纪要”中处理'; return }
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
    await router.push({ path: `/assistant/${created.id}`, query: mode.value === 'review' && route.query.mode === 'review' ? { mode: 'review' } : {} })
    void sessions.refresh()
  } catch (e) { error.value = failure(e) }
  finally { working.value = false }
}
// 第一句问题作为会话标题（P4）
async function sent(text: string) {
  template.value = null  // 模板只作用于这一条；追问回到自由对话，需要时再点快捷标签（U5-09）
  await refresh()
  if (session.value && session.value.title === '新会话' && text.trim()) {
    try { session.value = await assistantApi.patch(id.value, { title: text.trim().replace(/\s+/g, ' ').slice(0, 20) }); void sessions.refresh() } catch { /* 标题不影响使用 */ }
  }
}
const filePanel = ref<InstanceType<typeof FilePanel>>(), drawerOpen = ref(false)
// 审核视图首页（还没有会话）：拖入、点选或粘贴文件才建会话，不在进入页面时建空会话（V32-B-01 1）
const reviewPicker = ref<HTMLInputElement>(), reviewDragging = ref(false)
function reviewFiles(list: File[]) { if (list.length && !working.value) void start({ text: '', template: null, files: list }) }
function reviewPicked(event: Event) { const input = event.target as HTMLInputElement, list = Array.from(input.files ?? []); input.value = ''; reviewFiles(list) }
function reviewDrop(event: DragEvent) { reviewDragging.value = false; reviewFiles(Array.from(event.dataTransfer?.files ?? [])) }
usePasteFiles(reviewFiles, () => !id.value && mode.value === 'review' && !working.value)
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
const homeLike = computed(() => !!session.value && !messages.value.length && mode.value === 'ask' && !anchor.value)
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
    <main v-loading="loading" class="dialogue" :class="{ 'is-home': (mode === 'ask' && homeLike) || (mode === 'ask' && !session && !id) }">
      <el-alert v-if="error" type="error" :closable="false" :title="error" />
      <template v-if="session">
        <template v-if="homeLike">
          <div v-if="canReview" class="mode-row"><SiSegmented v-model="modeModel" :options="modeOptions" ariaLabel="工作台视图" /></div>
          <h1 class="home-title">今天要处理什么？</h1>
        </template>
        <div v-else class="toolbar">
          <el-button class="title" link @click="rename">{{ session.title }}</el-button>
          <SiSegmented v-if="canReview" v-model="modeModel" :options="modeOptions" size="small" ariaLabel="工作台视图" />
          <el-button size="small" @click="drawerOpen = true"><Files :size="14" :stroke-width="1.75" /><span class="files-label">材料 {{ session.files.length }}</span></el-button>
          <el-button link type="danger" :disabled="working || session.review_running" @click="remove(session)">删除会话</el-button>
        </div>
        <template v-if="mode === 'ask'">
          <div v-if="anchor" class="anchor" role="status">
            <span class="anchor-tag">讨论问题</span>
            <span class="anchor-title" :title="anchor.title">问题 {{ anchor.no }}「{{ anchor.title }}」</span>
            <span class="anchor-run">第 {{ anchor.runNo }} 轮审核</span>
            <el-button link type="primary" size="small" @click="modeModel = 'review'">回到审核结果</el-button>
            <el-button link size="small" aria-label="解除讨论锚点" title="解除后回到普通对话" @click="anchor = null"><X :size="14" :stroke-width="2" />解除</el-button>
          </div>
          <Transition name="rise"><Conversation v-if="!homeLike" :messages="shown" :session-id="anchor ? `${id}:${anchor.id}` : id" :hint="anchor ? '围绕这个问题继续讨论。发给模型的是该问题的结论与相关条款（已脱敏），不含其他条款。' : undefined" :draftable="!!anchor && !session.original_deleted" @draft="openDraft" @stop="stop" @retry="retry" /></Transition>
        </template>
        <!-- 切换到审核视图时只隐藏输入框，不卸载，未发出的文字保留（V32-B-01 评审） -->
        <Composer v-show="mode === 'ask'" :key="id" v-model:template="template" :session="session" :anchor="anchor" :generating="generating" :asr-enabled="session.asr_enabled" :show-templates="!messages.length && !anchor" :uploading="uploading" :initial="initial" @sent="sent" @stop="stop" @busy="refresh" @files="addFiles" @open="openFile" @remove="filePanel?.remove($event)" @confirm="filePanel?.openConfirm()" @retry="filePanel?.retryMedia($event)" />
        <ReviewPane v-if="mode === 'review'" :key="id" :session="session" @refresh="refresh" @confirm="filePanel?.openConfirm()" @pick="filePanel?.pick('doc')" @discuss="discuss" />
      </template>
      <div v-else-if="!loading && !id" class="home">
        <div v-if="canReview" class="mode-row"><SiSegmented v-model="modeModel" :options="modeOptions" ariaLabel="工作台视图" /></div>
        <h1>{{ mode === 'review' ? '上传材料，发起全面审核' : '今天要处理什么？' }}</h1>
        <Composer v-show="mode === 'ask'" v-model:template="template" :session="null" :generating="null" :asr-enabled="sessions.asrEnabled" show-templates :uploading="homeUpload" @start="start" />
        <div v-if="mode === 'review'" class="review-home">
          <input ref="reviewPicker" type="file" multiple hidden accept=".docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png" @change="reviewPicked">
          <button type="button" class="dropzone" :class="{ 'is-dragging': reviewDragging }" :disabled="working" @click="reviewPicker?.click()" @dragover.prevent="reviewDragging = true" @dragleave="reviewDragging = false" @drop.prevent="reviewDrop">
            <span class="upload-icon"><Upload :size="24" :stroke-width="1.75" /></span>
            <span class="drop-title">将合同、报价单拖到此处，或 <em>点击选择</em></span>
            <span class="drop-sub">文件在本机解析、确认脱敏后，才会发送给模型；也可以先直接提问</span>
          </button>
          <p v-if="homeUpload !== null" class="hint" role="status">正在上传 {{ homeUpload }}%</p>
        </div>
      </div>
    </main>
    <DraftDialog v-model:open="draftOpen" :anchor="anchor" :message="draftMessage" />
    <FilePanel v-if="session" ref="filePanel" :key="id" v-model:open="drawerOpen" :session="session" @refresh="refresh" />
  </div>
</template>
<style scoped>
.assistant-page { height: 100%; box-sizing: border-box; padding-top: 20px; padding-bottom: 20px; overflow: hidden; }
.dialogue { height: 100%; min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--el-border-color-light); }
.title { flex: 1; min-width: 0; justify-content: flex-start; white-space: normal; overflow-wrap: anywhere; text-align: left; font: var(--si-font-h2); }
.files-label { margin-left: 6px; }
.anchor { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; padding: 8px 20px; border-bottom: 1px solid var(--el-border-color-light); background: var(--si-surface-2); font: var(--si-font-caption); }
.anchor-tag { padding: 1px 8px; border-radius: var(--el-border-radius-base); background: color-mix(in srgb, var(--si-blue) 14%, transparent); color: var(--si-blue-ink); font: var(--si-font-label); }
.anchor-title { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--si-ink-1); }
.anchor-run { color: var(--si-ink-3); }
.mode-row { display: flex; justify-content: center; margin-bottom: 16px; }
.review-home { width: min(640px, calc(100% - 48px)); margin: 0 auto; }
.dropzone { width: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 200px; padding: 24px; box-sizing: border-box; border: 1.5px dashed var(--el-border-color); border-radius: var(--si-radius-card); background: transparent; color: inherit; font: var(--si-font-body); cursor: pointer; transition: border-color var(--si-dur-1) var(--si-ease), background-color var(--si-dur-1) var(--si-ease); }
.dropzone:hover:not(:disabled), .dropzone.is-dragging { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.dropzone:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.upload-icon { width: 44px; height: 44px; display: grid; place-items: center; background: var(--el-color-primary-light-9); color: var(--el-color-primary); border-radius: 12px; }
.drop-title em { color: var(--el-color-primary); font-style: normal; }
.drop-sub { color: var(--si-ink-3); font: var(--si-font-caption); }
.home { flex: 1; display: flex; flex-direction: column; justify-content: center; padding-bottom: 8vh; }
.home h1, .home-title { margin: 0 0 20px; text-align: center; font: var(--si-font-title-lg); color: var(--si-ink-1); }
/* 首页布局：标题与输入框居中，位置与未建会话时相同 */
.dialogue.is-home { justify-content: center; padding-bottom: 8vh; }
.rise-enter-active { transition: opacity var(--si-dur-2, 240ms) var(--si-ease), transform var(--si-dur-2, 240ms) var(--si-ease); }
.rise-enter-from { opacity: 0; transform: translateY(12px); }
@media (prefers-reduced-motion: reduce) { .rise-enter-active { transition: none; } }
</style>
