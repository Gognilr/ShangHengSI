<script setup lang="ts">
// 会议纪要（V31-U5，侧栏独立菜单）：录音或上传 → 本机转写 → 校对与确认脱敏 → 生成纪要 → 导出
// 底层沿用智能助手的会话接口（purpose=meeting）；录音与逐字稿只在本机，生成纪要时才发脱敏后的文字
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { ArrowLeft, Mic, UploadCloud } from '@lucide/vue'
import { assistantApi, confirmationTask, type Message, type Session, type SessionItem } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'
import { saveBlob } from '@/utils/save'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import EmptyState from '@/components/EmptyState.vue'
import MediaRow from '@/components/assistant/MediaRow.vue'
import MessageItem from '@/components/assistant/MessageItem.vue'
import TranscriptEditor from '@/components/assistant/TranscriptEditor.vue'
import MeetingRecorder from '@/components/meeting/MeetingRecorder.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'

const route = useRoute(), router = useRouter()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), recent = ref<SessionItem[]>([])
const asrEnabled = ref(true), loading = ref(false), error = ref(''), uploading = ref<number | null>(null)
const AUDIO = '.mp3,.m4a,.wav,.amr,.aac,.mp4', picker = ref<HTMLInputElement>(), dragging = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, version = 0

const audio = computed(() => session.value?.files.find(f => f.media) ?? null)
const minutes = computed(() => messages.value.filter(m => m.role === 'assistant' && m.template === 'minutes'))
const generating = computed(() => minutes.value.some(m => ['queued', 'streaming'].includes(m.status)))
const view = ref<'transcript' | 'minutes'>('transcript')

function failure(e: unknown) { return e instanceof ApiError ? (e.status === 404 ? '会议记录不存在或已被删除' : e.message) : '加载失败，请刷新页面后重试' }
async function loadRecent() {
  try { const page = await assistantApi.list(1, '', 'meeting'); recent.value = page.items; asrEnabled.value = page.asr_enabled } catch (e) { error.value = failure(e) }
}
async function load() {
  const current = ++version
  clearTimeout(timer); error.value = ''
  if (!id.value) { session.value = null; messages.value = []; await loadRecent(); return }
  loading.value = !session.value
  try {
    const [detail, list] = await Promise.all([assistantApi.get(id.value), assistantApi.messages(id.value)])
    if (current !== version) return
    session.value = detail; messages.value = list.items; asrEnabled.value = detail.asr_enabled
  } catch (e) { if (current === version) error.value = failure(e) }
  finally { if (current === version) { loading.value = false; schedule() } }
}
// 解码 / 转写中每 3 秒、纪要生成中每秒刷新；页面隐藏时不刷新
function schedule() {
  clearTimeout(timer)
  const media = audio.value?.media
  const waiting = session.value?.status === 'parsing' || (media && !['done', 'failed'].includes(media.stage))
  if (!document.hidden && (waiting || generating.value)) timer = setTimeout(load, generating.value ? 1000 : 3000)
}
watch(id, () => { session.value = null; view.value = 'transcript'; void load() }, { immediate: true })
function visibility() { if (!document.hidden) void load() }
document.addEventListener('visibilitychange', visibility)
onBeforeUnmount(() => { clearTimeout(timer); ++version; document.removeEventListener('visibilitychange', visibility) })

// ---- 加入录音：没有会议记录时先建一条 ----
async function upload(files: File[]) {
  const file = files[0]
  if (!file) return
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!AUDIO.split(',').includes('.' + ext)) { error.value = '请选择会议录音（mp3、m4a、wav、amr、aac、mp4）'; return }
  if (file.size > 500 * 1024 * 1024) { error.value = '录音单个不能超过 500 MB'; return }
  error.value = ''; uploading.value = 0
  try {
    const target = session.value?.id ?? (await assistantApi.create('meeting')).id
    await assistantApi.addFiles(target, [file], (loaded, total) => { uploading.value = Math.round(loaded / total * 100) })
    if (target !== id.value) await router.push(`/meeting/${target}`); else await load()
  } catch (e) { error.value = failure(e) }
  finally { uploading.value = null }
}
function picked(event: Event) { const input = event.target as HTMLInputElement, files = Array.from(input.files ?? []); input.value = ''; void upload(files) }
function drop(event: DragEvent) { dragging.value = false; void upload(Array.from(event.dataTransfer?.files ?? [])) }

// ---- 确认脱敏与生成纪要 ----
const confirmOpen = ref(false), task = computed(() => session.value ? confirmationTask(session.value) : null)
function confirmed() { confirmOpen.value = false; void load() }
const models = ref<AvailableModel[]>([]), modelId = ref<number>(), sending = ref(false)
async function loadModels() {
  try { models.value = (await modelsApi.list('assist')).items; modelId.value ??= models.value.find(m => m.is_default)?.id ?? models.value[0]?.id }
  catch (e) { error.value = failure(e) }
}
void loadModels()
async function generate() {
  if (!session.value || !audio.value || sending.value) return
  sending.value = true; error.value = ''
  const body = { model_id: modelId.value, message: '', template: 'minutes' as const, params: { file_ids: [audio.value.id] }, use_kb: false, use_history: false }
  try {
    const preview = await assistantApi.preview(session.value.id, body)
    // 纪要请求本身不含用户文字，正常不会有疑似项；有时交给助手页逐项决定
    if (preview.suspects.length) { error.value = '本次请求中有需要确认的内容，请在智能助手中打开此会议继续'; return }
    await assistantApi.send(session.value.id, { ...body, decisions: [] })
    view.value = 'minutes'; await load()
  } catch (e) { error.value = failure(e) }
  finally { sending.value = false }
}
async function downloadTranscript() {
  if (!session.value || !audio.value) return
  try { const data = await assistantApi.transcript(session.value.id, audio.value.id); await saveBlob(data.blob, data.filename) } catch (e) { error.value = failure(e) }
}
async function retry() { if (session.value && audio.value) { try { session.value = await assistantApi.retranscribe(session.value.id, audio.value.id); schedule() } catch (e) { error.value = failure(e) } } }
async function remove() {
  if (!session.value) return
  try { await ElMessageBox.confirm(`将删除“${session.value.title}”的录音、逐字稿与纪要，无法恢复。`, '删除会议记录', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) } catch { return }
  try { await assistantApi.remove(session.value.id); await router.push('/meeting') } catch (e) { error.value = failure(e) }
}
async function stopMessage(mid: number) { if (session.value) { await assistantApi.stop(session.value.id, mid); await load() } }
async function retryMessage(mid: number) { if (session.value) { await assistantApi.retry(session.value.id, mid); await load() } }
</script>

<template>
  <!-- 首页：录音或上传，下面是最近的会议 -->
  <div v-if="!id" class="page page--standard meeting-home">
    <h1>会议纪要</h1>
    <p class="lede">录音在本机转写，不出服务器；生成纪要时只把校对并确认脱敏后的文字发给模型。</p>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <EmptyState v-if="!asrEnabled" :icon="Mic" title="录音转写未启用" description="请联系系统管理员启用本机转写后再使用会议纪要。" />
    <template v-else>
      <div class="drop" :class="{ dragging }" role="button" tabindex="0" @click="picker?.click()" @keydown.enter="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
        <UploadCloud :size="28" :stroke-width="1.5" />
        <p class="drop-title">{{ uploading !== null ? `正在上传 ${uploading}%` : '拖入会议录音，或点击选择' }}</p>
        <p class="hint">mp3、m4a、wav、amr、aac、mp4，单个不超过 500 MB、3 小时</p>
      </div>
      <input ref="picker" class="file-input" type="file" :accept="AUDIO" @change="picked">
      <div class="or"><span>或</span></div>
      <MeetingRecorder @recorded="upload([$event])" />
    </template>
    <section v-if="recent.length" class="recent">
      <h2>最近的会议</h2>
      <router-link v-for="item in recent" :key="item.id" :to="`/meeting/${item.id}`" class="recent-row"><span class="name">{{ item.title }}</span><span class="hint num">{{ formatDateTime(item.last_activity_at) }}</span></router-link>
    </section>
  </div>

  <!-- 单个会议：转写进度 → 校对 → 纪要 -->
  <div v-else v-loading="loading" class="page page--split meeting-page">
    <div class="toolbar">
      <el-button text @click="router.push('/meeting')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="gap">全部会议</span></el-button>
      <h2 class="title">{{ session?.title }}</h2>
      <SiSegmented v-if="minutes.length" v-model="view" ariaLabel="查看" :options="[{ value: 'transcript', label: '逐字稿' }, { value: 'minutes', label: `会议纪要 ${minutes.length}` }]" />
      <el-button link type="danger" @click="remove">删除</el-button>
    </div>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <template v-if="session && audio?.media">
      <div v-if="audio.media.stage !== 'done'" class="status">
        <MediaRow :file="audio" :busy="false" @retry="retry" @download="downloadTranscript" />
        <p v-if="audio.media.stage !== 'failed'" class="hint">可以离开页面，转写在服务器上继续；完成后回来校对。</p>
        <el-button v-else-if="audio.media.error !== 'decode_failed'" @click="retry">重新转写</el-button>
      </div>
      <div v-else-if="view === 'transcript'" class="workspace">
        <TranscriptEditor :session="session" :file="audio" @refresh="load" @confirm="confirmOpen = true">
          <template #actions>
            <div v-if="audio.confirmed_at" class="generate">
              <el-select v-model="modelId" size="small" aria-label="生成纪要用的模型" class="model"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
              <el-button type="primary" :loading="sending || generating" @click="generate">{{ minutes.length ? '重新生成纪要' : '生成会议纪要' }}</el-button>
              <p class="hint">发给模型的只有脱敏后的逐字稿</p>
            </div>
          </template>
        </TranscriptEditor>
      </div>
      <div v-else class="minutes">
        <div class="reading">
          <MessageItem v-for="m in [...minutes].reverse()" :key="m.id" :message="m" :session-id="session.id" :last="true" @stop="stopMessage" @retry="retryMessage" />
          <el-button link @click="router.push(`/assistant/${session.id}`)">在智能助手中就这场会议继续提问</el-button>
        </div>
      </div>
    </template>
    <el-dialog v-if="task" v-model="confirmOpen" title="确认脱敏" fullscreen destroy-on-close append-to-body :close-on-click-modal="false"><div class="confirmation"><ConfirmWorkbench :task="task" mode="assistant" @confirmed="confirmed" @stale="load" /></div></el-dialog>
  </div>
</template>

<style scoped>
.meeting-home h1 { margin: 24px 0 4px; font: var(--si-font-title-lg); }
.lede { margin: 0 0 24px; color: var(--si-ink-3); }
.drop { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 40px 16px; border: 1.5px dashed var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-surface-1); color: var(--si-ink-3); cursor: pointer; transition: border-color var(--si-dur-1) var(--si-ease), background-color var(--si-dur-1) var(--si-ease); }
.drop:hover, .drop.dragging { border-color: var(--si-blue); background: color-mix(in srgb, var(--si-blue) 6%, var(--si-surface-1)); }
.drop-title { margin: 4px 0 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.drop .hint { margin: 0; }
.file-input { display: none; }
.or { display: flex; align-items: center; gap: 12px; margin: 16px 0; color: var(--si-ink-4); font: var(--si-font-caption); }
.or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--si-hair); }
.recent { margin-top: 32px; }
.recent h2 { font: var(--si-font-h3); margin: 0 0 8px; }
.recent-row { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: var(--el-border-radius-base); color: var(--si-ink-1); text-decoration: none; }
.recent-row:hover { background: var(--si-surface-2); }
.recent-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meeting-page { height: 100%; box-sizing: border-box; padding-top: 16px; padding-bottom: 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 12px; }
.title { flex: 1; min-width: 0; margin: 0; font: var(--si-font-h2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gap { margin-left: 6px; }
.status { max-width: 520px; margin: 48px auto; padding: 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
.workspace { flex: 1; min-height: 0; }
.generate { display: flex; flex-direction: column; gap: 8px; width: 100%; margin-top: 8px; }
.generate .model { width: 100%; }
.generate .hint { margin: 0; }
.minutes { flex: 1; min-height: 0; overflow-y: auto; }
.reading { max-width: 760px; margin: 0 auto; padding-bottom: 24px; }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }
</style>
