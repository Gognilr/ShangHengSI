<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 翻译（V31-U5，侧栏独立菜单）：拖入文件 → 本机解析与确认脱敏 → 开始翻译 → 导出译文或双语 Word
// 底层沿用智能助手会话（purpose=translate）的翻译模板；本地离线翻译接入后在此选择“本地 · 不外发”（V31-U5-06）
import DialogBackHeader from '@/components/ui/DialogBackHeader.vue'
import { usePasteFiles } from '@/composables/usePasteFiles'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessageBox } from 'element-plus'
import { ArrowLeft, ChevronRight, Ellipsis, Languages, Lock, Sparkles, UploadCloud } from '@lucide/vue'
import { assistantApi, confirmationTask, type Message, type Session, type SessionItem, type TranslationEngine } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ApiError } from '@/api/http'
import NotFound from '@/views/NotFound.vue'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import { rowTrip, translateTrip } from '@/utils/trip'
import TransLine from '@/components/assistant/TransLine.vue'
import TripTimeline, { type TimelineItem } from '@/components/TripTimeline.vue'
import MessageItem from '@/components/assistant/MessageItem.vue'

const route = useRoute(), router = useRouter(), auth = useAuthStore()
const canCloud = computed(() => auth.can('use.translate'))
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), recent = ref<SessionItem[]>([])
const loading = ref(false), error = ref(''), missing = ref(false), uploading = ref<number | null>(null), dragging = ref(false)
const engine = ref<TranslationEngine | ''>(canCloud.value ? 'cloud' : 'local'), mtAvailable = ref(false)
const switchingEngine = ref(false)
let enginePicked = false
// 入口页选的方式带到翻译页
const queryEngine = () => route.query.engine === 'local' || route.query.engine === 'cloud' ? route.query.engine : null
const localAvailable = computed(() => session.value?.mt_enabled ?? mtAvailable.value)
const LANGS = ['英文', '中文', '日文', '韩文', '俄文', '西班牙文', '法文', '德文', '阿拉伯文']
const target = ref(typeof route.query.to === 'string' && LANGS.includes(route.query.to) ? route.query.to : '英文')
const confirmOpen = ref(false)
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png', picker = ref<HTMLInputElement>()
let timer: ReturnType<typeof setTimeout> | undefined, version = 0, autoOpened = false

const file = computed(() => session.value?.files[0] ?? null)
const results = computed(() => messages.value.filter(m => m.role === 'assistant' && m.template === 'translate'))
const running = computed(() => results.value.some(m => ['queued', 'streaming'].includes(m.status)))
const engineOptions = computed(() => [
  { value: 'local', label: '本地 · 不外发', disabled: switchingEngine.value || running.value || !localAvailable.value },
  ...(canCloud.value ? [{ value: 'cloud', label: '云端 · 脱敏后发送', disabled: switchingEngine.value || running.value }] : []),
])
const latest = computed(() => results.value[results.value.length - 1])
const percent = computed(() => { const e = latest.value?.extras; return running.value && e?.segments_total ? Math.round((e.segments_done ?? 0) / e.segments_total * 100) : null })
// 工具条（二版第二节）：本机三站或云端五站，百分比写在当前站旁；映射在 utils/trip.ts
const trip = computed(() => translateTrip({ status: session.value?.status ?? 'active', engine: engine.value || null, translationStatus: latest.value?.status, confirmed: file.value ? !!file.value.confirmed_at || engine.value === 'local' : undefined, percent: percent.value }))
const step = computed(() => !file.value ? 'empty' : session.value?.status === 'parsing' ? 'parsing' : session.value?.status === 'parse_failed' ? 'failed'
  : !engine.value ? 'choose' : engine.value === 'local' && file.value.parse_state === 'done' ? 'ready' : !file.value.confirmed_at ? 'confirm' : 'ready')

const recentRows = computed<TimelineItem[]>(() => recent.value.map(item => ({ key: item.id, title: item.title, sub: item.translation_engine === 'cloud' ? '云端翻译' : item.translation_engine === 'local' ? '本机翻译' : '', trip: rowTrip(item), time: item.last_activity_at, mod: 'trans' as const })))
function failure(e: unknown) { return e instanceof ApiError ? (e.status === 404 ? '翻译记录不存在或已被删除' : e.message) : '加载失败，请刷新页面后重试' }
async function load() {
  const current = ++version
  clearTimeout(timer); error.value = ''; missing.value = false
  if (!id.value) { session.value = null; messages.value = []; try { const list = await assistantApi.list(1, '', 'translate'); recent.value = list.items; mtAvailable.value = list.mt_enabled; if (!enginePicked) { engine.value = list.mt_enabled || !canCloud.value ? 'local' : 'cloud'; enginePicked = true } } catch (e) { error.value = failure(e) } return }
  loading.value = !session.value
  try {
    const [detail, list] = await Promise.all([assistantApi.get(id.value), assistantApi.messages(id.value)])
    if (current !== version) return
    session.value = detail; messages.value = list.items
    if (!enginePicked) {
      const selected = detail.translation_engine ?? queryEngine()
      engine.value = selected === 'cloud' && !canCloud.value ? '' : selected ?? ''
      enginePicked = true
    }
  } catch (e) {
    // 不存在、已删除或不是本人的：整页显示“页面不存在”（V33-B-03）
    if (current === version) { if (e instanceof ApiError && e.status === 404) missing.value = true; else error.value = failure(e) }
  }
  finally { if (current === version) { loading.value = false; schedule() } }
}
function schedule() {
  clearTimeout(timer)
  if (!document.hidden && (session.value?.status === 'parsing' || running.value)) timer = setTimeout(load, running.value ? 1000 : 2000)
}
watch(id, () => { session.value = null; messages.value = []; confirmOpen.value = false; autoOpened = false; enginePicked = false; void load() }, { immediate: true })
function visibility() { if (!document.hidden) void load() }
document.addEventListener('visibilitychange', visibility)
onBeforeUnmount(() => { clearTimeout(timer); ++version; document.removeEventListener('visibilitychange', visibility) })

async function upload(files: File[]) {
  const picked = files[0]
  if (!picked) return
  const ext = picked.name.split('.').pop()?.toLowerCase() ?? ''
  if (!DOC.split(',').includes('.' + ext)) { error.value = '不支持该文件格式'; return }
  if (picked.size > 30 * 1024 * 1024) { error.value = '文件单个不能超过 30 MB'; return }
  error.value = ''; uploading.value = 0
  try {
    const created = await assistantApi.create('translate', false, engine.value || undefined)
    await assistantApi.addFiles(created.id, [picked], (loaded, total) => { uploading.value = Math.round(loaded / total * 100) })
    await router.push({ path: `/translate/${created.id}`, query: { to: target.value, engine: engine.value } })
  } catch (e) { error.value = failure(e) }
  finally { uploading.value = null }
}
function picked(event: Event) { const input = event.target as HTMLInputElement, files = Array.from(input.files ?? []); input.value = ''; void upload(files) }
function drop(event: DragEvent) { dragging.value = false; void upload(Array.from(event.dataTransfer?.files ?? [])) }
usePasteFiles(files => void upload(files), () => !id.value && uploading.value === null)
// 粘贴文字：服务器把文字生成 docx 当原件，之后与上传文件同一流程（V321Q-36）
const textOpen = ref(false), pasted = ref('')
async function uploadText() {
  const text = pasted.value.trim()
  if (!text || uploading.value !== null) return
  error.value = ''; uploading.value = 0
  try {
    const created = await assistantApi.create('translate', false, engine.value || undefined)
    await assistantApi.addText(created.id, text)
    textOpen.value = false; pasted.value = ''
    await router.push({ path: `/translate/${created.id}`, query: { to: target.value, engine: engine.value } })
  } catch (e) { error.value = failure(e) }
  finally { uploading.value = null }
}
function pasteText(event: ClipboardEvent) {
  if (id.value || textOpen.value || uploading.value !== null || event.clipboardData?.files.length) return
  const el = event.target as HTMLElement | null
  if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
  const text = event.clipboardData?.getData('text/plain') ?? ''
  if (!text.trim()) return
  event.preventDefault(); pasted.value = text.slice(0, 20000); textOpen.value = true
}
onMounted(() => document.addEventListener('paste', pasteText))
onBeforeUnmount(() => document.removeEventListener('paste', pasteText))

// 解析完成后自动打开确认脱敏（只打开一次，关掉后可点按钮再开）
const task = computed(() => session.value ? confirmationTask(session.value) : null)
watch(step, value => { if (value === 'confirm' && engine.value === 'cloud' && !autoOpened) { autoOpened = true; confirmOpen.value = true } })
watch(engine, value => { enginePicked = true; if (value === 'local') confirmOpen.value = false })
async function chooseEngine(value: string) {
  if ((value !== 'local' && value !== 'cloud') || switchingEngine.value || engine.value === value) return
  const selected = session.value?.id
  if (!selected) { engine.value = value; enginePicked = true; return }
  switchingEngine.value = true; error.value = ''
  try {
    const updated = await assistantApi.patch(selected, { translation_engine: value })
    if (id.value !== selected) return
    session.value = updated; engine.value = value
  } catch (e) { if (id.value === selected) error.value = failure(e) }
  finally { switchingEngine.value = false }
}
function confirmed() { confirmOpen.value = false; void load() }

const models = ref<AvailableModel[]>([]), modelId = ref<number>(), sending = ref(false)
if (canCloud.value) void (async () => { try { models.value = (await modelsApi.list('assist')).items; modelId.value = models.value.find(m => m.is_default)?.id ?? models.value[0]?.id } catch (e) { error.value = failure(e) } })()
async function start() {
  if (!session.value || !file.value || !engine.value || switchingEngine.value || sending.value || (engine.value === 'local' ? !localAvailable.value : !canCloud.value)) return
  sending.value = true; error.value = ''
  const body = { model_id: engine.value === 'cloud' ? modelId.value : undefined, message: '', template: 'translate' as const, params: { file_id: file.value.id, target: target.value, engine: engine.value }, use_kb: false }
  try {
    const preview = await assistantApi.preview(session.value.id, body)
    if (preview.suspects.length) { error.value = '本次请求中有需要确认的内容，请在智能助手中打开继续'; return }
    await assistantApi.send(session.value.id, { ...body, decisions: [] })
    if (session.value.title === '新会话') session.value = await assistantApi.patch(session.value.id, { title: file.value.original_name })
    await load()
  } catch (e) { error.value = failure(e) }
  finally { sending.value = false }
}
async function remove() {
  if (!session.value) return
  try { await ElMessageBox.confirm(`将删除“${session.value.title}”的原文、译文与对照表，无法恢复。`, '删除翻译记录', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) } catch { return }
  try { await assistantApi.remove(session.value.id); await router.push('/translate') } catch (e) { error.value = failure(e) }
}
async function stopMessage(mid: number) { if (session.value) { await assistantApi.stop(session.value.id, mid); await load() } }
async function retryMessage(mid: number) { if (session.value) { await assistantApi.retry(session.value.id, mid); await load() } }
</script>


<template>
  <!-- 入口页（澄明二版 6.14）：开关决定往返线的颜色 → 目标语言与拖放区 → 按天的最近的翻译 -->
  <div v-if="!id" class="entry-page translate-home si-stagger">
    <h1 class="entry-title">翻译</h1>
    <p class="entry-lede">本机翻译全程不外发；云端翻译先遮住名称，复检后发送，回来在本机还原。译稿请逐段核对。</p>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" class="err" />
    <div class="tmode" role="group" aria-label="翻译方式">
      <button type="button" :class="{ on: engine === 'local' }" :disabled="!localAvailable" :aria-pressed="engine === 'local'" @click="chooseEngine('local')">
        <span class="mod-ic m-local"><Lock :size="18" :stroke-width="1.75" /></span>
        <span><b>本机翻译</b><span class="aux">{{ localAvailable ? '全程本机 · 不外发 · 万字约一小时' : '本地翻译未启用，请联系管理员' }}</span></span>
      </button>
      <button v-if="canCloud" type="button" :class="{ on: engine === 'cloud' }" :aria-pressed="engine === 'cloud'" @click="chooseEngine('cloud')">
        <span class="mod-ic m-cloud"><Sparkles :size="18" :stroke-width="1.75" /></span>
        <span><b>云端翻译</b><span class="aux">遮住、复检后发送 · 速度快</span></span>
      </button>
    </div>
    <TransLine :cloud="engine === 'cloud'" />
    <div class="card-in">
      <div class="setup"><span class="label">译成</span><el-select v-model="target" aria-label="目标语言" class="lang"><el-option v-for="l in LANGS" :key="l" :value="l" :label="l" /></el-select></div>
      <div class="drop" :class="{ dragging, busy: uploading !== null }" role="button" tabindex="0" @click="picker?.click()" @keydown.enter.self="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
        <UploadCloud :size="20" :stroke-width="1.75" />
        <span class="drop-title">{{ uploading !== null ? `正在上传 ${uploading}%` : dragging ? '松开即可加入' : '拖入文件，或直接粘贴文字' }}</span>
        <el-button v-if="uploading === null" link type="primary" @click.stop="textOpen = true">粘贴文字翻译</el-button>
        <span v-if="uploading !== null" class="bar" aria-hidden="true"><i :style="{ width: `${uploading}%` }" /></span>
      </div>
      <p class="hint formats">Word、PDF、Excel、WPS、图片，单个不超过 30 MB · 也可以按 Ctrl+V 粘贴</p>
    </div>
    <input ref="picker" class="file-input" type="file" :accept="DOC" @change="picked">
    <el-dialog v-model="textOpen" title="粘贴要翻译的文字" width="640px" append-to-body destroy-on-close>
      <el-input v-model="pasted" type="textarea" :rows="12" maxlength="20000" show-word-limit placeholder="把要翻译的文字粘贴到这里，最多 2 万字。空行分段，后续与上传文件相同：本机读取、确认遮住的内容、翻译。" aria-label="要翻译的文字" />
      <template #footer><el-button @click="textOpen = false">取消</el-button><el-button type="primary" :loading="uploading !== null" :disabled="!pasted.trim()" @click="uploadText">下一步</el-button></template>
    </el-dialog>
    <section v-if="recent.length" class="recent">
      <div class="recent-head"><h2>最近的翻译</h2><router-link :to="{ name: 'history', query: { type: 'translate' } }" class="all">全部历史<ChevronRight :size="14" :stroke-width="1.75" /></router-link></div>
      <TripTimeline :items="recentRows" @open="it => router.push(`/translate/${it.key}`)" />
    </section>
  </div>

  <!-- 翻译页（工作区）：工具条（步骤、百分比）+ 设置条 + 原文译文同一张纸面 -->
  <NotFound v-else-if="missing" kind="content" />
  <div v-else v-loading="loading" class="page page--split translate-page">
    <div class="toolbar">
      <el-button text @click="router.push('/translate')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="back-t">全部翻译</span></el-button>
      <h2 class="title" :title="file?.original_name ?? session?.title">{{ file?.original_name ?? session?.title }}</h2>
      <SiTrip class="steps" :trip="trip" scale="bar" />
      <el-dropdown trigger="click" @command="remove">
        <el-button size="small" aria-label="更多操作"><Ellipsis :size="14" :stroke-width="1.75" /></el-button>
        <template #dropdown><el-dropdown-menu><el-dropdown-item command="remove"><span class="danger-item">删除翻译记录</span></el-dropdown-item></el-dropdown-menu></template>
      </el-dropdown>
    </div>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" />
    <div v-if="session" class="body">
      <div class="bar-card">
        <SiSegmented :model-value="engine" :options="engineOptions" ariaLabel="翻译方式" @update:model-value="chooseEngine" />
        <span v-if="!localAvailable" class="hint">本地翻译未启用，请联系管理员</span>
        <span class="grow" />
        <span v-if="step === 'choose'" class="hint">请选择翻译方式</span>
        <template v-else-if="step === 'confirm'"><el-button type="primary" @click="confirmOpen = true">去确认遮住的内容</el-button></template>
        <template v-else-if="step === 'ready'">
          <el-select v-model="target" class="lang" aria-label="目标语言"><el-option v-for="l in LANGS" :key="l" :value="l" :label="`译成${l}`" /></el-select>
          <el-select v-if="engine === 'cloud'" v-model="modelId" class="model" aria-label="翻译用的模型"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
          <el-button type="primary" :loading="sending || running" :disabled="engine === 'cloud' ? !modelId : !localAvailable" @click="start">{{ results.length ? '重新翻译' : '开始翻译' }}</el-button>
          <span v-if="engine === 'cloud' && !modelId" class="hint">尚未配置云端模型，请联系管理员</span>
        </template>
        <span v-else-if="step === 'parsing'" class="hint">正在本机读取文件 · 可以离开页面</span>
      </div>
      <p v-if="step === 'failed'" class="fail">{{ session.error_message || '文件读取失败，请删除后重新上传' }}</p>
      <SiNotice v-if="engine === 'local' && (running || step === 'ready')" type="warning" title="本机翻译较慢，可以先离开" description="关闭页面不影响翻译，稍后从“最近的翻译”回来查看。" :closable="false" class="local-note" />
      <div class="reading">
        <p v-if="!results.length && step === 'ready'" class="hint ready-hint"><Languages :size="16" :stroke-width="1.75" />选择目标语言后点“开始翻译”。</p>
        <MessageItem v-for="m in [...results].reverse()" :key="m.id" :message="m" :session-id="session.id" :last="true" @stop="stopMessage" @retry="retryMessage" />
      </div>
    </div>
    <el-dialog v-if="task" v-model="confirmOpen" fullscreen destroy-on-close append-to-body :close-on-click-modal="false"><template #header="{ close }"><DialogBackHeader title="确认脱敏" back="返回翻译" @close="close" /></template><div class="confirmation"><ConfirmWorkbench :task="task" mode="assistant" @confirmed="confirmed" @stale="load" /></div></el-dialog>
  </div>
</template>

<style scoped>
.danger-item { color: var(--el-color-danger); }
.err { margin-bottom: 16px; }
.file-input { display: none; }
.tmode { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; max-width: 620px; margin-top: 24px; padding: 5px; border-radius: 16px; background: var(--si-sunken); }
.tmode:has(button:only-child) { grid-template-columns: 1fr; }
.tmode button { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; border: 0; border-radius: 12px; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out), box-shadow var(--si-d2) var(--si-ease-out); }
.tmode button.on { background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.tmode button:disabled { cursor: not-allowed; opacity: .6; }
.tmode button:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.tmode .mod-ic { flex: none; display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; }
.tmode .m-local { background: var(--si-blue-tint); color: var(--si-trip-l); }
.tmode .m-cloud { background: var(--si-ai-soft); color: var(--si-trip-m); }
.tmode b { display: block; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.tmode .aux { display: block; color: var(--si-ink-3); font: var(--si-font-caption); }
.card-in { margin-top: 20px; padding: 18px 20px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.setup { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.setup .label { font: var(--si-font-label); color: var(--si-ink-2); }
.lang { width: 150px; }
.model { width: 170px; }
.drop { position: relative; display: flex; align-items: center; justify-content: center; gap: 10px; height: 64px; overflow: hidden; border: 1.5px dashed var(--si-hair-strong); border-radius: 14px; color: var(--si-ink-3); cursor: pointer; transition: border-color var(--si-d2) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out); }
.drop:hover { border-color: var(--si-m-trans); }
.drop:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.drop.dragging { border-style: solid; border-color: var(--si-m-trans); background: var(--si-m-trans-soft); }
.drop.busy { cursor: progress; }
.drop-title { color: var(--si-ink-2); font: var(--si-font-body); }
.formats { margin: 8px 0 0; text-align: center; }
.recent { margin-top: 40px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.recent-head h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.recent-head .all { display: inline-flex; align-items: center; gap: 2px; color: var(--si-ink-3); font: var(--si-font-label-sm); text-decoration: none; }
.recent-head .all:hover { color: var(--si-blue-ink); }
.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--si-sunken); }
.bar i { display: block; height: 100%; background: var(--si-progress-flow, var(--si-brand-2)); transition: width var(--si-d3) var(--si-ease-out); }
.translate-page { height: 100%; box-sizing: border-box; padding-top: 12px; padding-bottom: 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 16px; min-height: 52px; }
.title { flex: 1; min-width: 0; margin: 0; font: var(--si-font-h2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.steps { flex: none; }
.back-t { margin-left: 6px; }
.body { flex: 1; min-height: 0; overflow-y: auto; }
.bar-card { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 10px 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.bar-card .grow { flex: 1; }
.bar-card .hint { margin: 0; }
.fail { margin: 12px 0 0; color: var(--si-danger-ink); font: var(--si-font-caption); }
.local-note { margin: 12px 0 0; }
.reading { max-width: 1280px; margin: 16px auto 0; }
.ready-hint { display: flex; align-items: center; gap: 6px; justify-content: center; margin: 32px 0; }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }

</style>
