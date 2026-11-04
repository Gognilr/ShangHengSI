<script setup lang="ts">
// 翻译（V31-U5，侧栏独立菜单）：拖入文件 → 本机解析与确认脱敏 → 开始翻译 → 导出译文或双语 Word
// 底层沿用智能助手会话（purpose=translate）的翻译模板；本地离线翻译接入后在此选择“本地 · 不外发”（V31-U5-06）
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { ArrowLeft, Languages, UploadCloud } from '@lucide/vue'
import { assistantApi, confirmationTask, type Message, type Session, type SessionItem } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import MessageItem from '@/components/assistant/MessageItem.vue'

const route = useRoute(), router = useRouter()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), recent = ref<SessionItem[]>([])
const loading = ref(false), error = ref(''), uploading = ref<number | null>(null), dragging = ref(false)
const engine = ref<'local' | 'cloud'>('cloud'), mtAvailable = ref(false)
let enginePicked = false
const localAvailable = computed(() => session.value?.mt_enabled ?? mtAvailable.value)
const LANGS = ['英文', '中文', '日文', '韩文', '俄文', '西班牙文', '法文', '德文', '阿拉伯文']
const target = ref(typeof route.query.to === 'string' && LANGS.includes(route.query.to) ? route.query.to : '英文')
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png', picker = ref<HTMLInputElement>()
let timer: ReturnType<typeof setTimeout> | undefined, version = 0, autoOpened = false

const file = computed(() => session.value?.files[0] ?? null)
const results = computed(() => messages.value.filter(m => m.role === 'assistant' && m.template === 'translate'))
const running = computed(() => results.value.some(m => ['queued', 'streaming'].includes(m.status)))
const step = computed(() => !file.value ? 'empty' : session.value?.status === 'parsing' ? 'parsing' : session.value?.status === 'parse_failed' ? 'failed'
  : engine.value === 'local' && file.value.parse_state === 'done' ? 'ready' : !file.value.confirmed_at ? 'confirm' : 'ready')

function failure(e: unknown) { return e instanceof ApiError ? (e.status === 404 ? '翻译记录不存在或已被删除' : e.message) : '加载失败，请刷新页面后重试' }
async function load() {
  const current = ++version
  clearTimeout(timer); error.value = ''
  if (!id.value) { session.value = null; messages.value = []; try { const list = await assistantApi.list(1, '', 'translate'); recent.value = list.items; mtAvailable.value = list.mt_enabled; if (!enginePicked) { engine.value = list.mt_enabled ? 'local' : 'cloud'; enginePicked = true } } catch (e) { error.value = failure(e) } return }
  loading.value = !session.value
  try {
    const [detail, list] = await Promise.all([assistantApi.get(id.value), assistantApi.messages(id.value)])
    if (current !== version) return
    session.value = detail; messages.value = list.items
    if (!enginePicked) { engine.value = detail.mt_enabled ? 'local' : 'cloud'; enginePicked = true }
  } catch (e) { if (current === version) error.value = failure(e) }
  finally { if (current === version) { loading.value = false; schedule() } }
}
function schedule() {
  clearTimeout(timer)
  if (!document.hidden && (session.value?.status === 'parsing' || running.value)) timer = setTimeout(load, running.value ? 1000 : 2000)
}
watch(id, () => { session.value = null; autoOpened = false; enginePicked = false; void load() }, { immediate: true })
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
    const created = await assistantApi.create('translate')
    await assistantApi.addFiles(created.id, [picked], (loaded, total) => { uploading.value = Math.round(loaded / total * 100) })
    await router.push({ path: `/translate/${created.id}`, query: { to: target.value } })
  } catch (e) { error.value = failure(e) }
  finally { uploading.value = null }
}
function picked(event: Event) { const input = event.target as HTMLInputElement, files = Array.from(input.files ?? []); input.value = ''; void upload(files) }
function drop(event: DragEvent) { dragging.value = false; void upload(Array.from(event.dataTransfer?.files ?? [])) }

// 解析完成后自动打开确认脱敏（只打开一次，关掉后可点按钮再开）
const confirmOpen = ref(false), task = computed(() => session.value ? confirmationTask(session.value) : null)
watch(step, value => { if (value === 'confirm' && engine.value === 'cloud' && !autoOpened) { autoOpened = true; confirmOpen.value = true } })
watch(engine, value => { enginePicked = true; if (value === 'local') confirmOpen.value = false })
function confirmed() { confirmOpen.value = false; void load() }

const models = ref<AvailableModel[]>([]), modelId = ref<number>(), sending = ref(false)
void (async () => { try { models.value = (await modelsApi.list('assist')).items; modelId.value = models.value.find(m => m.is_default)?.id ?? models.value[0]?.id } catch (e) { error.value = failure(e) } })()
async function start() {
  if (!session.value || !file.value || sending.value) return
  sending.value = true; error.value = ''
  const body = { model_id: engine.value === 'cloud' ? modelId.value : undefined, message: '', template: 'translate' as const, params: { file_id: file.value.id, target: target.value, engine: engine.value }, use_kb: false, use_history: false }
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
  <div v-if="!id" class="page page--standard translate-home">
    <h1>翻译</h1>
    <p class="lede">本地翻译只在本机处理原文；云端翻译须先确认脱敏。译稿请逐段核对名称、金额和业务条件。</p>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div class="setup"><span class="label">译成</span><el-select v-model="target" aria-label="目标语言" class="lang"><el-option v-for="l in LANGS" :key="l" :value="l" :label="l" /></el-select><span class="hint where">{{ localAvailable ? '本地 · 不外发' : '云端 · 脱敏后发送（本地未启用）' }}</span></div>
    <div class="drop" :class="{ dragging }" role="button" tabindex="0" @click="picker?.click()" @keydown.enter="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
      <UploadCloud :size="28" :stroke-width="1.5" />
      <p class="drop-title">{{ uploading !== null ? `正在上传 ${uploading}%` : '拖入要翻译的文件，或点击选择' }}</p>
      <p class="hint">Word、PDF、Excel、WPS、图片，单个不超过 30 MB</p>
    </div>
    <input ref="picker" class="file-input" type="file" :accept="DOC" @change="picked">
    <section v-if="recent.length" class="recent">
      <h2>最近的翻译</h2>
      <router-link v-for="item in recent" :key="item.id" :to="`/translate/${item.id}`" class="recent-row"><span class="name">{{ item.title }}</span><span class="hint num">{{ formatDateTime(item.last_activity_at) }}</span></router-link>
    </section>
  </div>

  <div v-else v-loading="loading" class="page page--split translate-page">
    <div class="toolbar">
      <el-button text @click="router.push('/translate')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="gap">全部翻译</span></el-button>
      <h2 class="title">{{ file?.original_name ?? session?.title }}</h2>
      <el-button link type="danger" @click="remove">删除</el-button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div v-if="session" class="body">
      <div class="setup">
        <el-radio-group v-model="engine" :disabled="running" aria-label="翻译方式">
          <el-radio-button value="local" :disabled="!localAvailable">本地 · 不外发</el-radio-button>
          <el-radio-button value="cloud">云端 · 脱敏后发送</el-radio-button>
        </el-radio-group>
        <span v-if="!localAvailable" class="hint">本地翻译未启用，请联系管理员</span>
      </div>
      <div class="steps">
        <div class="step" :class="{ done: step !== 'parsing' && step !== 'empty', active: step === 'parsing' }"><span class="n">1</span>本机解析{{ step === 'parsing' ? '中…' : step === 'failed' ? '失败' : '' }}</div>
        <div class="step" :class="{ done: step === 'ready', active: step === 'confirm' }"><span class="n">2</span>{{ engine === 'local' ? '原文留在本机，无需确认脱敏' : '确认脱敏' }}<el-button v-if="step === 'confirm'" size="small" type="primary" @click="confirmOpen = true">去确认</el-button></div>
        <div class="step" :class="{ active: step === 'ready' }"><span class="n">3</span>翻译
          <template v-if="step === 'ready'">
            <el-select v-model="target" size="small" class="lang" aria-label="目标语言"><el-option v-for="l in LANGS" :key="l" :value="l" :label="`译成${l}`" /></el-select>
            <el-select v-if="engine === 'cloud'" v-model="modelId" size="small" class="model" aria-label="翻译用的模型"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
            <el-button size="small" type="primary" :loading="sending || running" :disabled="engine === 'cloud' && !modelId" @click="start">{{ results.length ? '重新翻译' : '开始翻译' }}</el-button>
            <span v-if="engine === 'cloud' && !modelId" class="hint">尚未配置云端模型，请联系管理员</span>
          </template>
        </div>
      </div>
      <p v-if="step === 'failed'" class="hint">{{ session.error_message || '文件解析失败，请删除后重新上传' }}</p>
      <p v-if="engine === 'local'" class="hint">离线译稿须人工核对。当前测试机万字约需 65 分钟，可离开页面后从“最近的翻译”继续查看。</p>
      <div class="reading">
        <p v-if="!results.length && step === 'ready'" class="hint ready-hint"><Languages :size="16" :stroke-width="1.75" />选择目标语言后点“开始翻译”。</p>
        <MessageItem v-for="m in [...results].reverse()" :key="m.id" :message="m" :session-id="session.id" :last="true" @stop="stopMessage" @retry="retryMessage" />
      </div>
    </div>
    <el-dialog v-if="task" v-model="confirmOpen" title="确认脱敏" fullscreen destroy-on-close append-to-body :close-on-click-modal="false"><div class="confirmation"><ConfirmWorkbench :task="task" mode="assistant" @confirmed="confirmed" @stale="load" /></div></el-dialog>
  </div>
</template>


<style scoped>
.translate-home h1 { margin: 24px 0 4px; font: var(--si-font-title-lg); }
.lede { margin: 0 0 20px; color: var(--si-ink-3); }
.setup { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.setup .label { font: var(--si-font-label); color: var(--si-ink-2); }
.lang { width: 140px; }
.model { width: 150px; }
.where { margin: 0; }
.drop { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 40px 16px; border: 1.5px dashed var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-surface-1); color: var(--si-ink-3); cursor: pointer; transition: border-color var(--si-dur-1) var(--si-ease), background-color var(--si-dur-1) var(--si-ease); }
.drop:hover, .drop.dragging { border-color: var(--si-blue); background: color-mix(in srgb, var(--si-blue) 6%, var(--si-surface-1)); }
.drop-title { margin: 4px 0 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.drop .hint { margin: 0; }
.file-input { display: none; }
.recent { margin-top: 32px; }
.recent h2 { font: var(--si-font-h3); margin: 0 0 8px; }
.recent-row { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: var(--el-border-radius-base); color: var(--si-ink-1); text-decoration: none; }
.recent-row:hover { background: var(--si-surface-2); }
.recent-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.translate-page { height: 100%; box-sizing: border-box; padding-top: 16px; padding-bottom: 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 12px; }
.title { flex: 1; min-width: 0; margin: 0; font: var(--si-font-h2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gap { margin-left: 6px; }
.body { flex: 1; min-height: 0; overflow-y: auto; }
.steps { display: flex; flex-wrap: wrap; gap: 8px 24px; padding: 12px 16px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
.step { display: flex; align-items: center; gap: 8px; color: var(--si-ink-4); font: var(--si-font-label); }
.step.active { color: var(--si-ink-1); }
.step.done { color: var(--si-ok-ink); }
.n { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; border: 1px solid currentColor; font: var(--si-font-caption); }
.reading { max-width: 900px; margin: 16px auto 0; }
.ready-hint { display: flex; align-items: center; gap: 6px; justify-content: center; margin: 32px 0; }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }
</style>
