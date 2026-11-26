<script setup lang="ts">
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
import { formatDateTime } from '@/utils/time'
import { saveBlob } from '@/utils/save'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
import MediaRow from '@/components/assistant/MediaRow.vue'
import MessageItem from '@/components/assistant/MessageItem.vue'
import TranscriptEditor from '@/components/assistant/TranscriptEditor.vue'
import MeetingRecorder, { type Recorded } from '@/components/meeting/MeetingRecorder.vue'
import StepBar from '@/components/StepBar.vue'

const route = useRoute(), router = useRouter(), auth = useAuthStore()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), recent = ref<SessionItem[]>([])
const asrEnabled = ref(true), loading = ref(false), error = ref(''), missing = ref(false), uploading = ref<number | null>(null)
const AUDIO = '.mp3,.m4a,.wav,.amr,.aac,.mp4', picker = ref<HTMLInputElement>(), dragging = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined, version = 0

const audio = computed(() => session.value?.files.find(f => f.media) ?? null)
const minutes = computed(() => messages.value.filter(m => m.role === 'assistant' && m.template === 'minutes'))
const generating = computed(() => minutes.value.some(m => ['queued', 'streaming'].includes(m.status)))
const view = ref<'tools' | 'minutes'>('tools')
// 工具条步骤（澄明 8.6）：转写 → 校对 → 确认遮住的内容 → 生成纪要（模型步骤用紫色）
const stepTitles = ['转写', '校对', '确认遮住的内容', '生成纪要']
const stepIndex = computed(() => {
  const media = audio.value?.media
  if (!media || media.stage !== 'done') return 0
  if (!audio.value?.confirmed_at) return 1
  return minutes.value.length && !generating.value ? 4 : 3
})
// 只有系统在做事（转写、生成纪要）时转圈；校对、确认遮住等你操作时显示编号
const stepStatus = computed(() => audio.value?.media?.stage === 'failed' ? 'error' : stepIndex.value === 0 || (stepIndex.value === 3 && generating.value) ? 'process' : 'wait')

// 最近的会议：时长 · 发言人数，加状态徽标（设计稿 8.6 ①）
const MEETING_STATE = { transcribing: ['转写中', 'is-info'], failed: ['转写失败', 'is-hi'], proofread: ['转写完成，待校对', 'is-mid'], confirmed: ['待生成纪要', 'is-brand'], minuted: ['纪要已生成', 'is-ok'] } as const
function meetingSub(item: SessionItem) {
  const m = item.meeting
  if (!m?.duration_ms) return ''
  const minutes = Math.max(1, Math.round(m.duration_ms / 60000)), h = Math.floor(minutes / 60)
  return [h ? `${h} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`, m.speakers ? `${m.speakers} 位发言人` : ''].filter(Boolean).join(' · ')
}
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
watch(id, () => { session.value = null; view.value = 'tools'; void load() }, { immediate: true })
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
const models = ref<AvailableModel[]>([]), modelId = ref<number>(), sending = ref(false)
async function loadModels() {
  try { models.value = (await modelsApi.list('assist')).items; modelId.value ??= models.value.find(m => m.is_default)?.id ?? models.value[0]?.id }
  catch (e) { error.value = failure(e) }
}
void loadModels()
async function generate() {
  if (!session.value || !audio.value || sending.value) return
  sending.value = true; error.value = ''
  const body = { model_id: modelId.value, message: '', template: 'minutes' as const, params: { file_ids: [audio.value.id] }, use_kb: false }
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
  <!-- 入口页（澄明 8.6 ①）：现场录音 / 上传录音两张大卡，下面是最近的会议 -->
  <div v-if="!id" class="entry-page meeting-home si-stagger">
    <h1 class="entry-title">会议纪要</h1>
    <p class="entry-lede">录一场会或上传录音，在本机转写；校对并遮住名称后，才把文字交给模型生成纪要。</p>
    <el-alert v-if="error" type="error" :closable="false" :title="error" class="err" />
    <div v-if="!asrEnabled" class="entry-cards off-wrap">
      <div class="entry-card off" style="--mod: var(--si-m-meet); --mod-soft: var(--si-m-meet-soft)"><span class="ic"><Mic :size="26" :stroke-width="1.75" /></span><b class="ttl">现场录音</b><span class="hint">录音转写还没有启用</span></div>
      <div class="entry-card off" style="--mod: var(--si-m-meet); --mod-soft: var(--si-m-meet-soft)"><span class="ic"><UploadCloud :size="26" :stroke-width="1.75" /></span><b class="ttl">上传录音</b><span class="hint">录音转写还没有启用</span></div>
    </div>
    <p v-if="!asrEnabled" class="hint off-note">请联系系统管理员启用本机转写。</p>
    <div v-else class="entry-cards" style="--mod: var(--si-m-meet); --mod-soft: var(--si-m-meet-soft)">
      <MeetingRecorder v-if="recordingOwner" :key="recordingOwner" ref="recorder" :owner-id="recordingOwner" :busy="uploading !== null" @recorded="upload([$event.file], $event)" />
      <div v-show="!recorder?.open" class="entry-card pick" :class="{ dragging }" role="button" tabindex="0" @click="picker?.click()" @keydown.enter="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
        <span class="ic"><UploadCloud :size="26" :stroke-width="1.75" /></span>
        <b class="ttl">{{ uploading !== null ? (uploading < 100 ? `正在上传 ${uploading}%` : '上传完成，正在提交转写……') : dragging ? '松开即可加入' : '上传录音' }}</b>
        <span class="hint">拖入或点击选择 · mp3、m4a、wav、amr、aac、mp4 · 单个不超过 500 MB、3 小时</span>
        <span v-if="uploading !== null" class="bar" aria-hidden="true"><i :style="{ width: `${uploading}%` }" /></span>
      </div>
    </div>
    <input ref="picker" class="file-input" type="file" :accept="AUDIO" @change="picked">
    <section v-if="recent.length" class="recent-list" style="--mod: var(--si-m-meet); --mod-soft: var(--si-m-meet-soft)">
      <div class="rl-head"><h2>最近的会议</h2><router-link :to="{ name: 'history', query: { type: 'meeting' } }" class="all">全部历史<ChevronRight :size="14" :stroke-width="1.75" /></router-link></div>
      <div class="rl"><router-link v-for="item in recent" :key="item.id" :to="`/meeting/${item.id}`" class="li"><span class="mod-ic"><Mic :size="18" :stroke-width="1.75" /></span><span class="li-main"><b :title="item.title">{{ item.title }}</b><span v-if="meetingSub(item)" class="num">{{ meetingSub(item) }}</span></span><span v-if="item.meeting" class="si-badge" :class="MEETING_STATE[item.meeting.state][1]"><i class="d" />{{ MEETING_STATE[item.meeting.state][0] }}</span><span class="li-t num">{{ formatDateTime(item.last_activity_at) }}</span></router-link></div>
    </section>
  </div>

  <!-- 会议页（工作区）：顶部工具条（步骤）+ 左逐字稿 / 右栏纪要 -->
  <NotFound v-else-if="missing" kind="content" />
  <div v-else v-loading="loading" class="page page--split meeting-page">
    <div class="toolbar">
      <el-button text @click="router.push('/meeting')"><ArrowLeft :size="16" :stroke-width="1.75" /><span class="back-t">全部会议</span></el-button>
      <h2 class="title" :title="session?.title">{{ session?.title }}</h2>
      <StepBar class="steps" :active="stepIndex" :status="stepStatus" :titles="stepTitles" :ai="[3]" aria-label="会议步骤" />
      <el-dropdown trigger="click" @command="remove">
        <el-button size="small" aria-label="更多操作"><Ellipsis :size="14" :stroke-width="1.75" /></el-button>
        <template #dropdown><el-dropdown-menu><el-dropdown-item command="remove"><span class="danger-item">删除会议记录</span></el-dropdown-item></el-dropdown-menu></template>
      </el-dropdown>
    </div>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <template v-if="session && audio?.media">
      <div v-if="audio.media.stage !== 'done'" class="status">
        <MediaRow :file="audio" :busy="false" @retry="retry" @download="downloadTranscript" />
        <p v-if="audio.media.stage !== 'failed'" class="hint">可以离开页面，转写在服务器上继续；完成后回来校对。</p>
        <el-button v-else-if="audio.media.error !== 'decode_failed'" @click="retry">重新转写</el-button>
      </div>
      <div v-else class="workspace">
        <TranscriptEditor v-model:tab="view" :session="session" :file="audio" :minutes-count="minutes.length" @refresh="load" @confirm="confirmOpen = true">
          <template #minutes>
            <div v-if="!audio.confirmed_at" class="m-empty"><Sparkles :size="22" :stroke-width="1.75" /><b>校对完成后生成纪要</b><span class="hint">在“校对”页确认遮住的内容后，这里可以生成纪要。</span></div>
            <template v-else>
              <div class="generate">
                <el-select v-model="modelId" size="small" aria-label="生成纪要用的模型" class="model"><el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" /></el-select>
                <el-button type="primary" :loading="sending || generating" @click="generate">{{ minutes.length ? '重新生成纪要' : '生成会议纪要' }}</el-button>
                <span class="hint">AI 建议 · 请核实。发给模型的只有遮住名称后的逐字稿。</span>
              </div>
              <MessageItem v-for="m in [...minutes].reverse()" :key="m.id" :message="m" :session-id="session.id" :last="true" @stop="stopMessage" @retry="retryMessage" />
              <el-button v-if="minutes.length && auth.can('use.assistant')" link @click="router.push(`/assistant/${session.id}`)">在智能助手中就这场会议继续提问</el-button>
            </template>
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
.off-wrap { margin-top: 4px; }
.off-note { margin: 12px 0 0; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; border-radius: 0 0 16px 16px; background: var(--si-sunken); overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--si-progress-flow, var(--si-brand-2)); transition: width var(--si-d3) var(--si-ease-out); }
.meeting-page { height: 100%; box-sizing: border-box; padding-top: 12px; padding-bottom: 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 16px; min-height: 52px; }
.title { flex: 1; min-width: 0; margin: 0; font: var(--si-font-h2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.steps { flex: none; }
.back-t { margin-left: 6px; }
.status { width: min(520px, 100%); margin: 48px auto; padding: 24px; border: 1px solid var(--si-hair); border-radius: 18px; background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.workspace { flex: 1; min-height: 0; }
.generate { display: flex; flex-direction: column; gap: 8px; width: 100%; margin: 0 0 12px; }
.generate .model { width: 100%; }
.generate .hint { margin: 0; color: var(--si-ai-ink); }
.m-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 40px 12px; text-align: center; color: var(--si-ai); }
.m-empty b { color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.m-empty .hint { margin: 0; }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }
@media (max-width: 1279px) { .steps :deep(.t) { display: none; } .steps :deep(.current .t) { display: inline; } }
</style>
