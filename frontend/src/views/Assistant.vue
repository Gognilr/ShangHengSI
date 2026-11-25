<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Ellipsis, PanelRight, Sparkle } from '@lucide/vue'
import { assistantApi, isReviewSession, type Session, type SessionItem, type Message, type MessageSource, type Template } from '@/api/assistant'
import { ApiError } from '@/api/http'
import NotFound from '@/views/NotFound.vue'
import type { Finding } from '@/api/runs'
import { useAuthStore } from '@/stores/auth'
import { useSessionsStore } from '@/stores/sessions'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import ReviewPane from '@/components/workspace/ReviewPane.vue'
import ReviewSteps, { type ReviewProgress } from '@/components/workspace/ReviewSteps.vue'
import Conversation from '@/components/assistant/Conversation.vue'
import Composer, { type ComposerStart } from '@/components/assistant/Composer.vue'
import FilePanel from '@/components/assistant/FilePanel.vue'
import WorkbenchHome from '@/components/assistant/WorkbenchHome.vue'
import DraftDialog from '@/components/workspace/DraftDialog.vue'

// 文件审核的步骤条由审核视图报来，显示在顶部工具条（澄明 8.4）
const reviewProgress = ref<ReviewProgress | null>(null)
const route = useRoute(), router = useRouter(), sessions = useSessionsStore(), auth = useAuthStore()
// 会话的两种视图（V32-B-01）：直接提问 / 全面审核。切换只改视图，不外发、不清空输入；没有“文件审核”权限的人看不到审核视图；会话以外的审核首页在 /review（V34-A-03）。
// 工作台首页不再有切换（V34-A-04）；会话里没有审核过时工具条只放“全面审核 →”，审核过或打开过审核视图才显示两个页签
const canReview = computed(() => auth.can('use.review'))
const canAsk = computed(() => auth.can('use.assistant'))
const mode = computed<'ask' | 'review'>(() => {
  if (!canReview.value) return 'ask'
  if (!canAsk.value || route.query.mode === 'review') return 'review'
  if (route.query.mode === 'ask') return 'ask'
  return isReviewSession(session.value) ? 'review' : 'ask'
})
const modeModel = computed<'ask' | 'review'>({
  get: () => mode.value,
  set: value => { void router.replace({ path: route.path, query: { mode: value } }) },
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
// 侧栏高亮：打开过审核视图或发起过审核的会话归“文件审核”（V34-A-03）
watch(() => [id.value, mode.value, isReviewSession(session.value), session.value?.review_running] as const, ([sid, m, runs, running]) => {
  if (sid && (m === 'review' || runs || running)) sessions.markReview(sid)
}, { immediate: true })
watch(() => session.value?.review_running, (running, was) => {
  if (was && !running && session.value) { reviewDone.value = mode.value !== 'review' || document.hidden; void sessions.refresh() }
  syncTitle()
})
watch(reviewDone, syncTitle)
function seeResult() { modeModel.value = 'review' }
const showTabs = computed(() => canAsk.value && canReview.value && (reviewSeen.value || isReviewSession(session.value) || !!session.value?.review_running))
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
      if (delta.crosscheck) item.crosscheck = delta.crosscheck
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
const initial = ref<{ text: string; submitted: boolean; modelId?: number } | null>(null), homeUpload = ref<number | null>(null)
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
    initial.value = { text: payload.text, modelId: payload.modelId, submitted: payload.action === 'send' }
    await router.push(`/assistant/${created.id}`)
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
const askComposer = ref<InstanceType<typeof Composer>>()
const askWaiting = ref(false), confirmedTick = ref(0)
function cancelWaiting() { askComposer.value?.cancelWaiting(); askWaiting.value = false }
async function attachmentsConfirmed() {
  const selected = id.value
  await refresh()
  if (selected === id.value && mode.value === 'ask') confirmedTick.value++
}
watch(mode, value => { if (value === 'review') cancelWaiting() }, { flush: 'sync' })
const filePanel = ref<InstanceType<typeof FilePanel>>(), reviewPane = ref<InstanceType<typeof ReviewPane>>(), drawerOpen = ref(false)
function openReviewConfirm() { reviewPane.value?.openConfirm() }
// 回复依据（V321-E-13）：原文段落在讨论栏旁的原文画布定位，直接提问时打开“查看原文”；其余依据在依据抽屉中看原文
function cite(message: Message, source: MessageSource, anchor: HTMLElement, locate?: (loc: string) => void) {
  if (source.kind === 'para') { if (locate) locate(source.ref); else void filePanel.value?.locate(source.ref) }
  else void filePanel.value?.showSource(message.id, source, anchor)
}
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
// 对话页标题旁的灵紫小徽标：最近一条回复用的模型
const modelLabel = computed(() => [...messages.value].reverse().find(m => m.role === 'assistant' && m.model_label)?.model_label ?? '')
const generating = computed(() => messages.value.find(m => m.role === 'assistant' && ['queued', 'streaming'].includes(m.status))?.id ?? null)
// 在侧栏改名后同步标题
watch(() => sessions.items.find(item => item.id === id.value)?.title, title => { if (title && session.value) session.value.title = title })
watch(id, load, { immediate: true })
function visibility() {
  if (document.hidden) clearTimeout(timer)
  else { void poll(); if (reviewDone.value && mode.value === 'review') reviewDone.value = false }
}
onMounted(() => document.addEventListener('visibilitychange', visibility))
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', visibility); document.title = baseTitle; active = false; ++version; clearTimeout(timer); offsets.clear() })
</script>
<template>
  <NotFound v-if="missing" kind="content" />
  <div v-else v-loading="loading" class="assistant-page" :class="{ 'is-home': (session && homeLike) || (!session && !id), 'is-review': mode === 'review' }">
    <el-alert v-if="error" class="page-error" type="error" :closable="false" :title="error" />
    <template v-if="session">
      <!-- 只加了文件、还没发出第一条消息：保持首页构图，文件标签在输入框里（澄明 8.2） -->
      <WorkbenchHome v-if="homeLike" busy :has-files="session.files.length > 0" v-model:template="template" @review="seeResult">
        <Composer v-if="canAsk" ref="askComposer" :key="id" :active="mode === 'ask'" :confirmed-tick="confirmedTick" @waiting="askWaiting = $event" v-model:template="template" hero :session="session" :generating="generating" :asr-enabled="session.asr_enabled" show-templates :uploading="uploading" :initial="initial" @sent="sent" @stop="stop" @busy="refresh" @files="addFiles" @open="openFile" @remove="filePanel?.remove($event)" @confirm="filePanel?.openConfirm()" @retry="filePanel?.retryMedia($event)" />
      </WorkbenchHome>
      <template v-else>
        <header class="chat-head">
          <div class="title-col">
            <button type="button" class="title" title="修改会话标题" @click="rename">{{ session.title }}</button>
            <span v-show="mode === 'review'" id="ws-head-sub" class="head-sub" />
          </div>
          <span v-if="modelLabel && mode === 'ask'" class="ai-tag"><Sparkle :size="13" :stroke-width="2" />{{ modelLabel }}</span>
          <span class="head-gap" />
          <ReviewSteps v-if="mode === 'review' && reviewProgress" :progress="reviewProgress" />
          <SiSegmented v-if="showTabs" v-model="modeModel" :options="modeOptions" size="small" ariaLabel="会话视图" />
          <el-button v-else-if="canReview && canAsk" size="small" link @click="seeResult">全面审核 →</el-button>
          <div v-show="mode === 'review'" id="ws-head-actions" class="head-actions" />
          <el-button size="small" @click="drawerOpen = true"><PanelRight :size="14" :stroke-width="1.75" /><span class="files-label">文件 {{ session.files.length }}</span></el-button>
          <el-dropdown trigger="click" :disabled="working || session.review_running" @command="remove(session)">
            <el-button size="small" aria-label="更多操作"><Ellipsis :size="14" :stroke-width="1.75" /></el-button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="remove"><span class="danger-item">删除会话</span></el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </header>
        <div v-if="canReview && mode === 'ask' && (session.review_running || reviewDone)" class="review-note" role="status">
          <span v-if="session.review_running">审核进行中，完成后可以提问。进度和已出现的问题在“全面审核”里看。</span>
          <span v-else>审核已完成，可以在“全面审核”里看问题，也可以继续提问。</span>
          <el-button size="small" type="primary" link @click="seeResult">{{ session.review_running ? '查看进度' : '查看结果' }}</el-button>
        </div>
        <Conversation v-show="mode === 'ask'" :messages="shown" :session-id="id" :reading="session?.status === 'parsing'" @cite="cite" @stop="stop" @retry="retry" />
        <!-- 切换到审核视图时只隐藏输入框，不卸载，未发出的文字保留（V32-B-01 评审） -->
        <div v-if="canAsk" v-show="mode === 'ask'" class="dock">
          <Composer ref="askComposer" :key="id" :active="mode === 'ask'" :confirmed-tick="confirmedTick" @waiting="askWaiting = $event" v-model:template="template" :session="session" :generating="generating" :asr-enabled="session.asr_enabled" :uploading="uploading" :initial="initial" @sent="sent" @stop="stop" @busy="refresh" @files="addFiles" @open="openFile" @remove="filePanel?.remove($event)" @confirm="filePanel?.openConfirm()" @retry="filePanel?.retryMedia($event)" />
          <p class="dock-note">模型只看到遮住名称后的文字。AI 建议请结合原文核实。</p>
        </div>
        <ReviewPane ref="reviewPane" v-if="canReview && reviewSeen" v-show="mode === 'review'" :key="id" class="review-pane" :session="session" :discuss-id="anchor?.id" :active="mode === 'review'" @refresh="refresh" @pick="filePanel?.pick('doc')" @remove="filePanel?.remove($event)" @discuss="discuss" @close-discuss="anchor = null" @progress="reviewProgress = $event" @ask="modeModel = 'ask'">
          <template #discussion="{ finding, no, update, locate }">
            <Conversation :messages="threadOf(finding.id)" :session-id="id" :view-key="`${id}:${finding.id}`" hint="围绕这个问题继续讨论。发给模型的是该问题的结论与相关条款（已遮住名称与金额），不含其他条款。"
              :draftable="!session.original_deleted" :saved-id="finding.discussed_message_id" :saved-no="no" @draft="openDraft($event, update)" @cite="(m: Message, src: MessageSource, el: HTMLElement) => cite(m, src, el, locate)" @stop="stop" @retry="retry" />
            <Composer :key="`${id}:${finding.id}`" :template="null" :session="session" :anchor="anchor" :generating="generating" :asr-enabled="false" @sent="sent" @stop="stop" @busy="refresh" @confirm="filePanel?.openConfirm()" />
          </template>
        </ReviewPane>
      </template>
    </template>
    <!-- 工作台首页（澄明 8.2） -->
    <WorkbenchHome v-else-if="!loading && !id" v-model:template="template" @review="router.push('/review')">
      <Composer v-model:template="template" hero :session="null" :generating="null" :asr-enabled="sessions.asrEnabled" show-templates :uploading="homeUpload" @start="start" />
    </WorkbenchHome>
    <DraftDialog v-model:open="draftOpen" :anchor="anchor" :message="draftMessage" @saved="draftUpdate?.($event)" />
    <FilePanel v-if="session" ref="filePanel" :key="id" v-model:open="drawerOpen" :session="session" :review-mode="mode === 'review'" :continue-question="askWaiting" @refresh="refresh" @attachments-confirmed="attachmentsConfirmed" @cancel-waiting="cancelWaiting" @review-confirm="openReviewConfirm" />
  </div>
</template>
<style scoped>
.danger-item { color: var(--el-color-danger); }
.assistant-page { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 0; overflow: hidden; }
/* 首页与只加了文件的会话：整页可纵向滚动，氛围光在顶部 */
.assistant-page.is-home { display: block; overflow-y: auto; }
.page-error { position: absolute; top: 12px; left: 50%; z-index: 3; width: min(760px, calc(100% - 48px)); transform: translateX(-50%); }
/* 对话页（澄明 8.3）：顶部 64px 工具条，中间一栏 800，输入框固定在底部 */
.chat-head { display: flex; flex: none; align-items: center; gap: 12px; height: 64px; padding: 0 24px; border-bottom: 1px solid var(--si-hair); background: var(--si-surface-1); }
.title { min-width: 0; max-width: 46%; padding: 4px 8px; margin-left: -8px; overflow: hidden; border: 0; border-radius: 8px; background: none; color: var(--si-ink-1); font: var(--si-font-h3); text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: text; }
.title:hover { background: var(--si-hover-bg); }
.ai-tag { display: inline-flex; flex: none; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 999px; background: var(--si-ai-soft); color: var(--si-ai-ink); font: var(--si-font-badge); }
.ai-tag svg { color: var(--si-ai); }
.head-gap { flex: 1; }
.title-col { flex: 0 1 auto; min-width: 0; max-width: 40%; display: flex; flex-direction: column; align-items: stretch; }
.title-col .title { max-width: none; }
.head-sub { max-width: 100%; overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.head-sub:empty, .head-actions:empty { display: none; }
.head-actions { display: flex; align-items: center; gap: 8px; }
.chat-head :deep(.el-button + .el-button), .chat-head :deep(.el-dropdown) { margin-left: 0; }
.files-label { margin-left: 6px; }
.review-note { display: flex; align-items: center; justify-content: center; gap: 12px; width: min(800px, calc(100% - 48px)); margin: 16px auto 0; padding: 8px 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-card-2); color: var(--si-ink-2); font: var(--si-font-body); box-sizing: border-box; }
.dock { flex: none; width: min(800px, calc(100% - 48px)); margin: 0 auto; padding: 0 0 14px; }
.dock-note { margin: 8px 0 0; color: var(--si-ink-4); font: var(--si-font-caption); font-size: calc(12.5px * var(--si-fs)); text-align: center; }
.is-review .review-pane { flex: 1; min-height: 0; }
</style>
