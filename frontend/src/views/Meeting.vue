<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 会议纪要（V31-U5，侧栏独立菜单）：录音或上传 → 本机转写 → 校对与确认脱敏 → 生成纪要 → 导出
// 底层沿用智能助手的会话接口（purpose=meeting）；录音与逐字稿只在本机，生成纪要时才发脱敏后的文字
import DialogBackHeader from '@/components/ui/DialogBackHeader.vue'
import { usePasteFiles } from '@/composables/usePasteFiles'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessageBox } from 'element-plus'
import { ArrowLeft, ChevronRight, Ellipsis, Mic, Sparkles, UploadCloud } from '@lucide/vue'
import { assistantApi, confirmationTask, type Message, type Session, type SessionItem } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ApiError } from '@/api/http'
import NotFound from '@/views/NotFound.vue'
import { saveBlob } from '@/utils/save'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import MediaRow from '@/components/assistant/MediaRow.vue'
import MessageItem from '@/components/assistant/MessageItem.vue'
import TranscriptEditor from '@/components/assistant/TranscriptEditor.vue'
import MeetingRecorder, { type Recorded } from '@/components/meeting/MeetingRecorder.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import { meetingTrip, rowTrip } from '@/utils/trip'
import SiZone from '@/components/ui/SiZone.vue'
import VoiceBand from '@/components/meeting/VoiceBand.vue'
import TripTimeline, { type TimelineItem } from '@/components/TripTimeline.vue'

const route = useRoute(), router = useRouter(), auth = useAuthStore()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), recent = ref<SessionItem[]>([])
const asrEnabled = ref(true), loading = ref(false), error = ref(''), missing = ref(false), uploading = ref<number | null>(null)
const sending = ref(false), outboundBlocked = ref(false)
const AUDIO = '.mp3,.m4a,.wav,.amr,.aac,.mp4', picker = ref<HTMLInputElement>(), dragging = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, version = 0

const audio = computed(() => session.value?.files.find(f => f.media) ?? null)
const minutes = computed(() => messages.value.filter(m => m.role === 'assistant' && m.template === 'minutes'))
const latestMinutes = computed(() => minutes.value.at(-1))
const generating = computed(() => minutes.value.some(m => ['queued', 'streaming'].includes(m.status)))
const view = ref<'tools' | 'minutes'>('tools')
// 工具条（二版第二节）：本机转写 · 校对 · 确认遮住 · 复检 · 生成纪要 · 还原；映射在 utils/trip.ts
const trip = computed(() => {
  const media = audio.value?.media
  const state = !media ? null : media.stage === 'failed' ? 'failed' : media.stage !== 'done' ? 'transcribing'
    : !audio.value?.confirmed_at ? 'proofread' : latestMinutes.value?.status === 'done' ? 'minuted' : 'confirmed'
  return meetingTrip({
    state, status: session.value?.status, generating: generating.value || sending.value,
    percent: media?.stage === 'transcribing' && media.progress ? Math.round(media.progress) : null,
    minutesStatus: latestMinutes.value?.status,
    blocked: outboundBlocked.value || latestMinutes.value?.error_code === 'BLOCKED',
  })
})

// 最近的会议（二版 6.12）：按天的时间线，副标题写时长与发言人数，往返线说进度
function meetingSub(item: SessionItem) {
  const m = item.meeting
  if (!m?.duration_ms) return ''
  const minutes = Math.max(1, Math.round(m.duration_ms / 60000)), h = Math.floor(minutes / 60)
  return [h ? `${h} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`, m.speakers ? `${m.speakers} 位发言人` : ''].filter(Boolean).join(' · ')
}
const recentRows = computed<TimelineItem[]>(() => recent.value.map(item => ({ key: item.id, title: item.title, sub: meetingSub(item), trip: rowTrip(item), time: item.last_activity_at, mod: 'meet' as const })))
function failure(e: unknown) { return e instanceof ApiError ? (e.status === 404 ? '会议记录不存在或已被删除' : e.message) : '加载失败，请刷新页面后重试' }
async function loadRecent() {
  try { const page = await assistantApi.list(1, '', 'meeting'); recent.value = page.items.filter(i => i.file_count > 0).slice(0, 5); asrEnabled.value = page.asr_enabled } catch (e) { error.value = failure(e) }
}
async function load() {
  const current = ++version
  clearTimeout(timer); error.value = ''; missing.value = false
  if (!id.value) { session.value = null; messages.value = []; await loadRecent(); return }
  loading.value = !session.value
  try {
    const [detail, list] = await Promise.all([assistantApi.get(id.value), assistantApi.messages(id.value)])
    if (current !== version) return
    session.value = detail; messages.value = list.items; asrEnabled.value = detail.asr_enabled
  } catch (e) {
    // 不存在、已删除或不是本人的：整页显示“页面不存在”（V33-B-03）
    if (current === version) { if (e instanceof ApiError && e.status === 404) missing.value = true; else error.value = failure(e) }
  }
  finally { if (current === version) { loading.value = false; schedule() } }
}
// 解码 / 转写中每 3 秒、纪要生成中每秒刷新；页面隐藏时不刷新
function schedule() {
  clearTimeout(timer)
  const media = audio.value?.media
  const waiting = session.value?.status === 'parsing' || (media && !['done', 'failed'].includes(media.stage))
  if (!document.hidden && (waiting || generating.value)) timer = setTimeout(load, generating.value ? 1000 : 3000)
}
watch(id, () => { session.value = null; outboundBlocked.value = false; view.value = 'tools'; void load() }, { immediate: true })
function visibility() { if (!document.hidden) void load() }
document.addEventListener('visibilitychange', visibility)
onBeforeUnmount(() => { clearTimeout(timer); ++version; document.removeEventListener('visibilitychange', visibility) })

// ---- 加入录音：没有会议记录时先建一条 ----
const recorder = ref<InstanceType<typeof MeetingRecorder>>()
const recordingOwner = computed(() => auth.user?.id)
let uploadVersion = 0
watch(recordingOwner, () => { ++uploadVersion; uploading.value = null }, { flush: 'sync' })
async function upload(files: File[], recorded?: Recorded) {
  const file = files[0], ownerId = recordingOwner.value
  if (!file || !ownerId || uploading.value !== null || (recorded && recorded.ownerId !== ownerId)) return
  const currentRecorder = recorder.value, current = ++uploadVersion
  const active = () => current === uploadVersion
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!AUDIO.split(',').includes('.' + ext)) { error.value = '请选择会议录音（mp3、m4a、wav、amr、aac、mp4）'; return }
  if (file.size > 500 * 1024 * 1024) { error.value = '录音单个不能超过 500 MB'; return }
  error.value = ''; uploading.value = 0
  try {
    const target = session.value?.id ?? (await assistantApi.create('meeting')).id
    if (!active()) return
    // 现场录音时填的主题与参会人：尽力而为，失败不挡上传（会后仍可在逐字稿页修改）
    if (recorded?.topic) await assistantApi.patch(target, { title: recorded.topic }).catch(() => undefined)
    if (!active()) return
    if (recorded?.participants.length) await assistantApi.participants(target, recorded.participants).catch(() => undefined)
    if (!active()) return
    await assistantApi.addFiles(target, [file], (loaded, total) => { if (active()) uploading.value = Math.round(loaded / total * 100) }, recorded?.marks)
    if (recorded) await currentRecorder?.settle(true, recorded.startedAt)  // 只清掉上传时所属账号的录音
    if (active()) {
      if (target !== id.value) await router.push(`/meeting/${target}`); else await load()
    }
  } catch (e) {
    if (!active()) return
    error.value = failure(e)
    if (recorded) await currentRecorder?.settle(false, recorded.startedAt)
  } finally { if (active()) uploading.value = null }
}
function picked(event: Event) { const input = event.target as HTMLInputElement, files = Array.from(input.files ?? []); input.value = ''; void upload(files) }
function drop(event: DragEvent) { dragging.value = false; void upload(Array.from(event.dataTransfer?.files ?? [])) }
usePasteFiles(files => void upload(files), () => !id.value && asrEnabled.value && uploading.value === null)

// ---- 确认脱敏与生成纪要 ----
const confirmOpen = ref(false), task = computed(() => session.value ? confirmationTask(session.value) : null)
function confirmed() { confirmOpen.value = false; view.value = 'minutes'; void load() }
const models = ref<AvailableModel[]>([]), modelId = ref<number>()
async function loadModels() {
  try { models.value = (await modelsApi.list('assist')).items; modelId.value ??= models.value.find(m => m.is_default)?.id ?? models.value[0]?.id }
  catch (e) { error.value = failure(e) }
}
void loadModels()
async function generate() {
  if (!session.value || !audio.value || sending.value) return
  sending.value = true; outboundBlocked.value = false; error.value = ''
  const body = { model_id: modelId.value, message: '', template: 'minutes' as const, params: { file_ids: [audio.value.id] }, use_kb: false }
  try {
    const preview = await assistantApi.preview(session.value.id, body)
    // 纪要请求本身不含用户文字，正常不会有疑似项；有时交给助手页逐项决定
    if (preview.suspects.length) { error.value = '本次请求中有需要确认的内容，请在智能助手中打开此会议继续'; return }
    await assistantApi.send(session.value.id, { ...body, decisions: [] })
    view.value = 'minutes'; await load()
  } catch (e) {
    outboundBlocked.value = e instanceof ApiError && e.code === 'OUTBOUND_BLOCKED'
    error.value = failure(e)
  }
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
async function retryMessage(mid: number) {
  if (!session.value) return
  outboundBlocked.value = false
  try { await assistantApi.retry(session.value.id, mid); await load() }
  catch (e) {
    outboundBlocked.value = e instanceof ApiError && e.code === 'OUTBOUND_BLOCKED'
    error.value = failure(e)
  }
}
</script>


<template>
  <!-- 入口页（澄明二版 6.12）：声纹带（现场录音大按钮 + 上传录音拖放行），下面是按天的最近的会议 -->
  <div v-if="!id" class="entry-page meeting-home si-stagger">
    <h1 v-show="!recorder?.open" class="entry-title">会议纪要</h1>
    <p v-show="!recorder?.open" class="entry-lede">录音在本机转写，校对后再整理纪要。生成纪要时只发送遮住名称后的逐字稿。</p>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" class="err" />
    <div v-if="!asrEnabled" class="vband off">
      <VoiceBand />
      <span class="vb-rec off-rec" aria-hidden="true"><Mic :size="30" :stroke-width="1.75" /></span>
      <p class="off-note">本机转写尚未启用，请联系系统管理员启用。</p>
    </div>
    <template v-else>
      <MeetingRecorder v-if="recordingOwner" :key="recordingOwner" ref="recorder" :owner-id="recordingOwner" :busy="uploading !== null" @recorded="upload([$event.file], $event)">
        <template #drop>
          <div class="vb-drop" :class="{ dragging }" role="button" tabindex="0" @click="picker?.click()" @keydown.enter.self="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
            <span class="mod-ic"><UploadCloud :size="18" :stroke-width="1.75" /></span>
            <div class="vd-t"><b>{{ uploading !== null ? (uploading < 100 ? `正在上传 ${uploading}%` : '上传完成，正在提交转写……') : dragging ? '松开即可加入' : '或把录音文件拖到这里' }}</b><span>MP3、M4A、WAV、AMR · 单个不超过 500 MB、3 小时</span></div>
            <el-button :disabled="uploading !== null" @click.stop="picker?.click()">选择录音</el-button>
            <span v-if="uploading !== null" class="bar" aria-hidden="true"><i :style="{ width: `${uploading}%` }" /></span>
          </div>
        </template>
      </MeetingRecorder>
    </template>
    <input ref="picker" class="file-input" type="file" :accept="AUDIO" @change="picked">
    <section v-if="recent.length" v-show="!recorder?.open" class="recent">
      <div class="recent-head"><h2>最近的会议</h2><router-link :to="{ name: 'history', query: { type: 'meeting' } }" class="all">全部历史<ChevronRight :size="14" :stroke-width="1.75" /></router-link></div>
      <TripTimeline :items="recentRows" @open="it => router.push(`/meeting/${it.key}`)" />
    </section>
  </div>

  <!-- 会议页（工作区）：顶部工具条（步骤）+ 左逐字稿 / 右栏纪要 -->
  <NotFound v-else-if="missing" kind="content" />
  <div v-else v-loading="loading" class="page page--split meeting-page">
    <div class="toolbar">
      <el-button text @click="router.push('/meeting')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="back-t">全部会议</span></el-button>
      <div class="title" :title="session?.title"><h2>{{ session?.title }}</h2><span v-if="session && meetingSub(session)" class="num">{{ meetingSub(session) }}</span></div>
      <SiTrip class="steps" :trip="trip" scale="bar" />
      <el-dropdown trigger="click" @command="remove">
        <el-button size="small" aria-label="更多操作"><Ellipsis :size="14" :stroke-width="1.75" /></el-button>
        <template #dropdown><el-dropdown-menu><el-dropdown-item command="remove"><span class="danger-item">删除会议记录</span></el-dropdown-item></el-dropdown-menu></template>
      </el-dropdown>
    </div>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" />
    <template v-if="session && audio?.media">
      <div v-if="audio.media.stage !== 'done'" class="status">
        <MediaRow :file="audio" :busy="false" @retry="retry" @download="downloadTranscript" />
        <p v-if="audio.media.stage !== 'failed'" class="hint">可以离开页面，转写在服务器上继续；完成后回来校对。</p>
        <el-button v-else-if="audio.media.error !== 'decode_failed'" @click="retry">重新转写</el-button>
      </div>
      <div v-else class="workspace">
        <TranscriptEditor v-model:tab="view" :session="session" :file="audio" :minutes-count="minutes.length" @refresh="load" @confirm="confirmOpen = true">
          <template #minutes>
            <SiZone kind="ai" title="纪要 · 模型生成" class="minutes-zone">
              <SiNotice v-if="!audio.confirmed_at" type="info" title="校对后再生成纪要" description="在“校对”页确认遮住的内容后，这里可以生成纪要。生成时只发送遮住名称后的逐字稿，回来在本机还原。" :closable="false" />
              <template v-else>
                <div class="generate">
                  <el-select v-model="modelId" size="small" aria-label="生成纪要用的模型" class="model"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
                  <el-button type="primary" :loading="sending || generating" @click="generate"><Sparkles :size="14" :stroke-width="1.75" />{{ minutes.length ? '重新生成纪要' : '生成会议纪要' }}</el-button>
                  <span class="hint">AI 建议 · 请核实。发给模型的只有遮住名称后的逐字稿；待办写明负责人和期限。</span>
                </div>
                <MessageItem v-for="m in [...minutes].reverse()" :key="m.id" :message="m" :session-id="session.id" :last="true" @stop="stopMessage" @retry="retryMessage" />
                <el-button v-if="minutes.length && auth.can('use.assistant')" link @click="router.push(`/assistant/${session.id}`)">在智能助手中就这场会议继续提问</el-button>
              </template>
            </SiZone>
          </template>
        </TranscriptEditor>
      </div>
    </template>
    <el-dialog v-if="task" v-model="confirmOpen" fullscreen destroy-on-close append-to-body :close-on-click-modal="false"><template #header="{ close }"><DialogBackHeader title="确认脱敏" back="返回会议纪要" @close="close" /></template><div class="confirmation"><ConfirmWorkbench :task="task" mode="assistant" @confirmed="confirmed" @stale="load" /></div></el-dialog>
  </div>
</template>

<style scoped>
.danger-item { color: var(--el-color-danger); }
.file-input { display: none; }
.err { margin-bottom: 16px; }
.vband { position: relative; padding: 34px 36px 26px; overflow: hidden; border: 1px solid var(--si-hair); border-radius: 22px; background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.vband.off { filter: grayscale(1); opacity: .7; }
.off-rec { position: absolute; left: 50%; top: 56px; display: grid; place-items: center; width: 76px; height: 76px; margin-left: -38px; border-radius: 50%; background: var(--si-m-meet); color: var(--si-white); box-shadow: 0 0 0 10px var(--si-surface-1); }
.off-note { margin: 22px 0 0; text-align: center; color: var(--si-ink-2); font: var(--si-font-label); }
.vb-drop { position: relative; display: flex; align-items: center; gap: 14px; margin-top: 22px; padding: 14px 16px; overflow: hidden; border: 1.5px dashed var(--si-hair-strong); border-radius: 14px; background: var(--si-card-2); cursor: pointer; transition: border-color var(--si-d2) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out); }
.vb-drop:hover, .vb-drop.dragging { border-color: var(--si-m-meet); }
.vb-drop.dragging { border-style: solid; background: var(--si-m-meet-soft); }
.vb-drop:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.vb-drop .mod-ic { flex: none; display: grid; place-items: center; width: 36px; height: 36px; border-radius: var(--si-radius-control); background: var(--si-m-meet-soft); color: var(--si-m-meet); }
.vd-t { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.vd-t b { color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.vd-t span { color: var(--si-ink-3); font: var(--si-font-caption); }
.recent { margin-top: 40px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.recent-head h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.recent-head .all { display: inline-flex; align-items: center; gap: 2px; color: var(--si-ink-3); font: var(--si-font-label-sm); text-decoration: none; }
.recent-head .all:hover { color: var(--si-blue-ink); }
.minutes-zone { margin-top: 4px; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--si-sunken); overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--si-progress-flow, var(--si-brand-2)); transition: width var(--si-d3) var(--si-ease-out); }
.meeting-page { height: 100%; box-sizing: border-box; padding-top: 12px; padding-bottom: 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 16px; min-height: 52px; }
.title { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.title h2 { margin: 0; font: var(--si-font-h2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.title span { color: var(--si-ink-3); font: var(--si-font-caption); }
.steps { flex: none; }
.back-t { margin-left: 6px; }
.status { width: min(520px, 100%); margin: 48px auto; padding: 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.workspace { flex: 1; min-height: 0; }
.generate { display: flex; flex-direction: column; gap: 8px; width: 100%; margin: 0 0 12px; }
.generate .model { width: 100%; }
.generate .hint { margin: 0; color: var(--si-ai-ink); }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }

</style>
