<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Ellipsis, Files, Upload } from '@lucide/vue'
import { assistantApi, type Session, type SessionItem, type Message, type MessageSource, type Template } from '@/api/assistant'
import { ApiError } from '@/api/http'
import NotFound from '@/views/NotFound.vue'
import type { Finding } from '@/api/runs'
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
// 问候只取北京时间和当前账号显示名，不发送到服务器（V33Q-14）。
const hourFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' })
const greetingHour = ref(Number(hourFormat.format(new Date())))
const greeting = computed(() => {
  const hour = greetingHour.value
  if (hour < 5 || hour >= 23) return { title: '夜深了', text: '先处理要紧的事，也记得早点休息。' }
  if (hour < 8) return { title: '早上好', text: '新的一天，想先从哪项工作开始？' }
  if (hour < 11) return { title: '上午好', text: '今天想先处理哪项工作？' }
  if (hour < 14) return { title: '中午好', text: '接下来，想先处理哪份材料？' }
  if (hour < 18) return { title: '下午好', text: '下午的工作，想先从哪件事开始？' }
  return { title: '晚上好', text: '今天还有哪项工作需要梳理？' }
})
const greetingTitle = computed(() => {
  const name = auth.user?.display_name?.trim() || auth.user?.username
  return name ? `${greeting.value.title}，${name}` : greeting.value.title
})
const greetingTimer = setInterval(() => { if (!document.hidden) greetingHour.value = Number(hourFormat.format(new Date())) }, 60000)
// 工作台两种视图（V32-B-01）：直接提问 / 全面审核。切换只改视图，不外发、不清空输入；没有“智能审核”权限的人看不到审核视图
const canReview = computed(() => auth.can('use.review'))
const mode = computed<'ask' | 'review'>(() => canReview.value && (route.query.mode === 'review' || !auth.can('use.assistant')) ? 'review' : 'ask')
const modeModel = computed<'ask' | 'review'>({
  get: () => mode.value,
  set: value => { void router.replace({ path: route.path, query: value === 'review' ? { mode: 'review' } : {} }) },
})
// 审核状态（V321-E-03）：页签小圆点、侧栏标记、浏览器标题三处同步；完成时人不在审核视图，页签与标题留“完成”提示，打开审核视图即清除
const reviewDone = ref(false)
const modeOptions = computed(() => [{ value: 'ask' as const, label: '直接提问' },
  { value: 'review' as const, label: '全面审核', ...(session.value?.review_running ? { badge: 'busy' as const, badgeLabel: '审核进行中' } : reviewDone.value ? { badge: 'done' as const, badgeLabel: '审核已完成' } : {}) }])
const baseTitle = document.title
function syncTitle() { document.title = session.value?.review_running ? `（审核中）${baseTitle}` : reviewDone.value ? `（审核完成）${baseTitle}` : baseTitle }
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), template = ref<Template | null>(null)
const loading = ref(false), error = ref(''), working = ref(false)
// 会话不存在、已删除或不是本人的：整页显示“页面不存在”（V33-B-03）
const missing = ref(false)
function gone(e: unknown) { return e instanceof ApiError && e.status === 404 }
// 按问题讨论（V32-B-02）：锚点只存在于本页，切换会话即清除。讨论在审核结果页右栏进行（V321-E-02），
// “直接提问”只显示普通对话；按问题的对话只在该问题的讨论栏里出现，互不混入
interface Anchor { id: string; no: string; title: string; runNo: number }
const anchor = ref<Anchor | null>(null)
const shown = computed(() => messages.value.filter(m => !m.finding_id))
const threadOf = (findingId: string) => messages.value.filter(m => m.finding_id === findingId)
function discuss(next: Anchor) { anchor.value = next }
// 审核视图第一次打开后只隐藏不卸载：来回切换视图时原文、问题栏的滚动与选中都保留
const reviewSeen = ref(mode.value === 'review')
watch(mode, value => { if (value === 'review') { reviewSeen.value = true; reviewDone.value = false } })
watch(() => session.value?.review_running, (running, was) => {
  if (was && !running && session.value) { reviewDone.value = mode.value !== 'review' || document.hidden; void sessions.refresh() }
  syncTitle()
})
watch(reviewDone, syncTitle)
function seeResult() { modeModel.value = 'review' }
// 本会话在侧栏的“审核完成”标记：打开即视为看过
watch(() => [sessions.finished, id.value], () => { if (id.value) sessions.seen(id.value) }, { immediate: true })
// 讨论回复保存为该问题的修改建议（V32-B-03）；保存结果直接回传给结果页更新该问题（V321-E-02）
const draftOpen = ref(false), draftMessage = ref<Message | null>(null)
let draftUpdate: ((finding: Finding) => void) | null = null
function openDraft(message: Message, update: (finding: Finding) => void) { draftMessage.value = message; draftUpdate = update; draftOpen.value = true }
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
  clearTimeout(timer); session.value = null; messages.value = []; offsets.clear(); drawerOpen.value = false; anchor.value = null; reviewDone.value = false; syncTitle()
  template.value = carryTemplate; carryTemplate = null
  loading.value = !!selected; error.value = ''; missing.value = false
  if (!selected || !active || current !== version) { loading.value = false; return }
  try {
    const [detail, response] = await Promise.all([assistantApi.get(selected), assistantApi.messages(selected)])
    if (!active || current !== version) return
    session.value = detail
    messages.value = response.items.map(item => ['queued', 'streaming'].includes(item.status) ? { ...item, content: '' } : item)
    void nextTick(() => { initial.value = null })
  } catch (e) { if (active && current === version) { if (gone(e)) missing.value = true; else error.value = failure(e) } }
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
      if (gone(e)) { session.value = null; missing.value = true; return }
      error.value = failure(e)
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
// 回复依据（V321-E-13）：原文段落在讨论栏旁的原文画布定位，直接提问时打开“查看原文”；其余依据在依据抽屉中看原文
function cite(message: Message, source: MessageSource, anchor: HTMLElement, locate?: (loc: string) => void) {
  if (source.kind === 'para') { if (locate) locate(source.ref); else void filePanel.value?.locate(source.ref) }
  else void filePanel.value?.showSource(message.id, source, anchor)
}
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
const homeLike = computed(() => !!session.value && !shown.value.length && mode.value === 'ask' && !session.value.review_running && !reviewDone.value)
const generating = computed(() => messages.value.find(m => m.role === 'assistant' && ['queued', 'streaming'].includes(m.status))?.id ?? null)
// 在侧栏改名后同步标题
watch(() => sessions.items.find(item => item.id === id.value)?.title, title => { if (title && session.value) session.value.title = title })
watch(id, load, { immediate: true })
function visibility() {
  greetingHour.value = Number(hourFormat.format(new Date()))
  if (document.hidden) clearTimeout(timer)
  else { void poll(); if (reviewDone.value && mode.value === 'review') reviewDone.value = false }
}
onMounted(() => document.addEventListener('visibilitychange', visibility))
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', visibility); document.title = baseTitle; active = false; ++version; clearTimeout(timer); clearInterval(greetingTimer); offsets.clear() })
</script>
<template>
  <NotFound v-if="missing" kind="content" />
  <div v-else class="page page--split assistant-page">
    <main v-loading="loading" class="dialogue" :class="{ 'is-home': (mode === 'ask' && homeLike) || (mode === 'ask' && !session && !id) }">
      <el-alert v-if="error" type="error" :closable="false" :title="error" />
      <template v-if="session">
        <template v-if="homeLike">
          <div v-if="canReview" class="mode-row"><SiSegmented v-model="modeModel" :options="modeOptions" ariaLabel="工作台视图" /></div>
          <h1 class="home-title greeting-title">{{ greetingTitle }}</h1>
          <p class="home-lede">{{ greeting.text }}</p>
        </template>
        <div v-else class="toolbar">
          <el-button class="title" link @click="rename">{{ session.title }}</el-button>
          <SiSegmented v-if="canReview" v-model="modeModel" :options="modeOptions" size="small" ariaLabel="工作台视图" />
          <el-button size="small" @click="drawerOpen = true"><Files :size="14" :stroke-width="1.75" /><span class="files-label">材料 {{ session.files.length }}</span></el-button>
          <el-dropdown trigger="click" :disabled="working || session.review_running" @command="remove(session)">
            <el-button size="small" aria-label="更多操作"><Ellipsis :size="14" :stroke-width="1.75" /></el-button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="remove"><span class="danger-item">删除会话</span></el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div>
        <div v-if="mode === 'ask' && (session.review_running || reviewDone)" class="review-note" role="status">
          <span v-if="session.review_running">审核进行中，完成后可提问。进度与已出现的问题在“全面审核”里看。</span>
          <span v-else>审核已完成，可以在“全面审核”里查看问题，也可以继续提问。</span>
          <el-button size="small" type="primary" link @click="seeResult">{{ session.review_running ? '查看进度' : '查看结果' }}</el-button>
        </div>
        <Transition name="rise"><Conversation v-if="!homeLike" v-show="mode === 'ask'" :messages="shown" :session-id="id" :reading="session?.status === 'parsing'" @cite="cite" @stop="stop" @retry="retry" /></Transition>
        <!-- 切换到审核视图时只隐藏输入框，不卸载，未发出的文字保留（V32-B-01 评审） -->
        <Composer v-show="mode === 'ask'" :key="id" v-model:template="template" :session="session" :generating="generating" :asr-enabled="session.asr_enabled" :show-templates="!shown.length" :uploading="uploading" :initial="initial" @sent="sent" @stop="stop" @busy="refresh" @files="addFiles" @open="openFile" @remove="filePanel?.remove($event)" @confirm="filePanel?.openConfirm()" @retry="filePanel?.retryMedia($event)" />
        <ReviewPane v-if="reviewSeen" v-show="mode === 'review'" :key="id" :session="session" :discuss-id="anchor?.id" :active="mode === 'review'" @refresh="refresh" @confirm="filePanel?.openConfirm()" @pick="filePanel?.pick('doc')" @remove="filePanel?.remove($event)" @discuss="discuss" @close-discuss="anchor = null">
          <template #discussion="{ finding, no, update, locate }">
            <Conversation :messages="threadOf(finding.id)" :session-id="`${id}:${finding.id}`" hint="围绕这个问题继续讨论。发给模型的是该问题的结论与相关条款（已脱敏），不含其他条款。"
              :draftable="!session.original_deleted" :saved-id="finding.discussed_message_id" :saved-no="no" @draft="openDraft($event, update)" @cite="(m: Message, src: MessageSource, el: HTMLElement) => cite(m, src, el, locate)" @stop="stop" @retry="retry" />
            <Composer :key="`${id}:${finding.id}`" :template="null" :session="session" :anchor="anchor" :generating="generating" :asr-enabled="false" @sent="sent" @stop="stop" @busy="refresh" @confirm="filePanel?.openConfirm()" />
          </template>
        </ReviewPane>
      </template>
      <div v-else-if="!loading && !id" class="home">
        <div v-if="canReview" class="mode-row"><SiSegmented v-model="modeModel" :options="modeOptions" ariaLabel="工作台视图" /></div>
        <h1 class="greeting-title">{{ greetingTitle }}</h1>
        <p class="home-lede">{{ mode === 'review' ? '上传材料，确认脱敏后发起全面审核。' : greeting.text }}</p>
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
    <DraftDialog v-model:open="draftOpen" :anchor="anchor" :message="draftMessage" @saved="draftUpdate?.($event)" />
    <FilePanel v-if="session" ref="filePanel" :key="id" v-model:open="drawerOpen" :session="session" @refresh="refresh" />
  </div>
</template>
<style scoped>
.danger-item { color: var(--el-color-danger); }
.assistant-page { height: 100%; box-sizing: border-box; padding-top: 20px; padding-bottom: 20px; overflow: hidden; }
.dialogue { height: 100%; min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--el-border-color-light); }
.title { flex: 1; min-width: 0; justify-content: flex-start; white-space: normal; overflow-wrap: anywhere; text-align: left; font: var(--si-font-h2); }
.review-note { display: flex; gap: 12px; align-items: center; justify-content: center; margin: 12px 20px 0; padding: 8px 12px; border-radius: var(--si-radius-card); background: var(--si-surface-1); border: 1px solid var(--si-hair); color: var(--si-ink-2); font: var(--si-font-body); }
.files-label { margin-left: 6px; }
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
.home .greeting-title, .greeting-title { max-width: 760px; margin: 0 auto 8px; padding: 0 24px; box-sizing: border-box; overflow-wrap: anywhere; }
.home-lede { margin: 0 24px 24px; text-align: center; font: var(--si-font-body); color: var(--si-ink-3); }
/* 首页布局：标题与输入框居中，位置与未建会话时相同 */
.dialogue.is-home { justify-content: center; padding-bottom: 8vh; }
.rise-enter-active { transition: opacity var(--si-dur-2, 240ms) var(--si-ease), transform var(--si-dur-2, 240ms) var(--si-ease); }
.rise-enter-from { opacity: 0; transform: translateY(12px); }
</style>
