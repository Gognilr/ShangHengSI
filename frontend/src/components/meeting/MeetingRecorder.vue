<script setup lang="ts">
// 浏览器内录音（V31-U5 第四节、V31-U5F-05 第 5.1 节；澄明二版 6.12）：16 kHz 单声道 PCM。
// 按下声纹带中间的大按钮即开始录音（用系统默认麦克风，首次会请求权限）；
// 录音中显示实时声波与大号计时，可暂停、打点、填主题与参会人；
// 分片实时写入浏览器本地存储（IndexedDB），刷新后可恢复；不做实时转写。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Flag, Mic, Pause, Play, RotateCcw, Square, Trash2, X } from '@lucide/vue'
import SiBadge from '@/components/ui/SiBadge.vue'
import VoiceBand from '@/components/meeting/VoiceBand.vue'
import { recordingStoreFor, type RecordingMeta } from '@/utils/recordingStore'
import { stamp } from '@/utils/transcript'

export interface Recorded { ownerId: string; startedAt: number; file: File; marks: number[]; topic: string; participants: string[] }
const props = defineProps<{ ownerId: string; busy: boolean }>()
const ownerId = props.ownerId
const recordingStore = recordingStoreFor(ownerId)
const emit = defineEmits<{ recorded: [value: Recorded] }>()
const RATE = 16000, LIMIT_S = 3 * 3600, FLUSH = RATE * 2, BARS = 96
type State = 'idle' | 'recording' | 'paused'
const state = ref<State>('idle'), seconds = ref(0), level = ref(0), error = ref('')
const bars = ref<number[]>(Array(BARS).fill(0))
const opening = ref(false)
const topic = ref(''), participants = ref<string[]>([]), marks = ref<number[]>([])
const saved = ref<RecordingMeta | null>(null), checkingSaved = ref(true)
let mounted = true, micRequest = 0
let ctx: AudioContext | null = null, stream: MediaStream | null = null, node: ScriptProcessorNode | null = null
let chunks: Int16Array[] = [], pending: Int16Array[] = [], pendingSamples = 0, samples = 0, startedAt = 0
const secure = typeof window !== 'undefined' && window.isSecureContext
const supported = secure && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
const httpsUrl = typeof location !== 'undefined' ? `https://${location.hostname}/meeting` : ''
const elapsed = computed(() => stamp(seconds.value * 1000))
const live = computed(() => state.value === 'recording' || state.value === 'paused')
const savedText = computed(() => saved.value ? `${stamp(saved.value.samples / RATE * 1000)}（${new Date(saved.value.startedAt).toLocaleString('zh-CN', { hour12: false })} 开始）` : '')

function describe(e: unknown): string {
  const name = e instanceof DOMException ? e.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return '麦克风权限被拒绝：请点击地址栏左侧的图标，把麦克风设为“允许”，然后重试'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return '没有找到麦克风：请连接麦克风后重试'
  if (name === 'NotReadableError' || name === 'AbortError') return '麦克风正被其他程序占用，请关闭占用它的程序后重试'
  return '无法使用麦克风，请检查设备与浏览器设置后重试'
}
function meta(): RecordingMeta {
  return { startedAt, samples, marks: [...marks.value], topic: topic.value.trim(), participants: [...participants.value] }
}
function guard(event: BeforeUnloadEvent) { event.preventDefault(); event.returnValue = '' }
onBeforeRouteLeave(async () => {
  if (!live.value) return true
  try { await ElMessageBox.confirm('正在录音，离开后录音会停止。已录的内容保存在本机，回到“会议纪要”页可以继续上传。', '离开页面', { type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续录音' }); return true } catch { return false }
})

// ---- 麦克风 ----
function process(event: AudioProcessingEvent) {
  const input = event.inputBuffer.getChannelData(0)
  let sum = 0
  for (let i = 0; i < input.length; i++) sum += input[i]! * input[i]!
  level.value = Math.min(1, Math.sqrt(sum / input.length) * 4)
  if (state.value === 'recording') {
    const out = new Int16Array(input.length)
    for (let i = 0; i < input.length; i++) { const v = Math.max(-1, Math.min(1, input[i]!)); out[i] = v < 0 ? v * 0x8000 : v * 0x7fff }
    chunks.push(out); pending.push(out); pendingSamples += out.length; samples += out.length
    seconds.value = samples / RATE
    bars.value = [...bars.value.slice(1), level.value]
    if (pendingSamples >= FLUSH) flush()
    if (seconds.value >= LIMIT_S) void stop()
  }
}
function flush() {
  if (!pending.length) return
  const merged = new Int16Array(pendingSamples)
  let at = 0
  for (const part of pending) { merged.set(part, at); at += part.length }
  pending = []; pendingSamples = 0
  void recordingStore.append(merged, meta())
}
async function openMic() {
  closeMic()
  const request = micRequest
  const inputStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false } })
  if (request !== micRequest) { inputStream.getTracks().forEach(t => t.stop()); return }
  stream = inputStream
  ctx = new AudioContext({ sampleRate: RATE })
  const source = ctx.createMediaStreamSource(stream)
  node = ctx.createScriptProcessor(4096, 1, 1)
  node.onaudioprocess = process
  source.connect(node); node.connect(ctx.destination)
}
function closeMic() {
  micRequest++
  node?.disconnect(); node = null
  stream?.getTracks().forEach(t => t.stop()); stream = null
  void ctx?.close(); ctx = null
  level.value = 0
}
// 按下录音大按钮：先打开麦克风，成功后立即开始录音
async function begin() {
  if (props.busy || checkingSaved.value || saved.value || opening.value) return
  error.value = ''
  if (!secure) { error.value = 'https'; return }
  if (!supported) { error.value = '当前浏览器不支持录音，请使用最新版 Edge 或 Chrome'; return }
  opening.value = true
  try { await openMic(); start() }
  catch (e) { error.value = describe(e); state.value = 'idle'; closeMic() }
  finally { opening.value = false }
}

// ---- 录音 ----
function start() {
  chunks = []; pending = []; pendingSamples = 0; samples = 0; seconds.value = 0; marks.value = []; startedAt = Date.now()
  bars.value = Array(BARS).fill(0)
  void recordingStore.begin(meta())
  state.value = 'recording'
  window.addEventListener('beforeunload', guard)
}
function pause() { state.value = state.value === 'recording' ? 'paused' : 'recording'; flush() }
function mark() {
  if (!live.value) return
  marks.value = [...marks.value, Math.round(samples / RATE * 1000)]
  void recordingStore.saveMeta(meta())
}
function unmark(index: number) { marks.value = marks.value.filter((_, i) => i !== index); void recordingStore.saveMeta(meta()) }
let metaTimer: ReturnType<typeof setTimeout> | undefined
function metaChanged() { clearTimeout(metaTimer); metaTimer = setTimeout(() => { if (live.value) void recordingStore.saveMeta(meta()) }, 500) }
function cleanNames() { participants.value = [...new Set(participants.value.map(n => n.trim()).filter(n => n.length >= 2 && n.length <= 40))].slice(0, 30); metaChanged() }

function wav(parts: Int16Array[], total: number): Blob {
  const header = new DataView(new ArrayBuffer(44)), bytes = total * 2
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) header.setUint8(offset + i, value.charCodeAt(i)) }
  text(0, 'RIFF'); header.setUint32(4, 36 + bytes, true); text(8, 'WAVE'); text(12, 'fmt ')
  header.setUint32(16, 16, true); header.setUint16(20, 1, true); header.setUint16(22, 1, true)
  header.setUint32(24, RATE, true); header.setUint32(28, RATE * 2, true); header.setUint16(32, 2, true); header.setUint16(34, 16, true)
  text(36, 'data'); header.setUint32(40, bytes, true)
  return new Blob([header.buffer, ...parts.map(c => c.buffer as ArrayBuffer)], { type: 'audio/wav' })
}
function fileName(time: number) {
  const now = new Date(time), pad = (n: number) => String(n).padStart(2, '0')
  return `现场录音_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.wav`
}
async function stop() {
  if (!live.value) return
  flush()
  const info = meta(), parts = chunks, total = samples
  closeMic(); state.value = 'idle'; chunks = []; samples = 0
  window.removeEventListener('beforeunload', guard)
  saved.value = info
  if (total) emit('recorded', { ownerId, startedAt: info.startedAt, file: new File([wav(parts, total)], fileName(info.startedAt), { type: 'audio/wav' }), marks: info.marks, topic: info.topic, participants: info.participants })
  else await settle(true, info.startedAt)
}
async function discard() {
  try { await ElMessageBox.confirm('放弃后这段录音无法找回。', '放弃录音', { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '继续录音' }) } catch { return }
  closeMic(); state.value = 'idle'; chunks = []; pending = []; samples = 0
  window.removeEventListener('beforeunload', guard)
  await settle(true, startedAt)
}

// ---- 防丢：上传成功后清除；失败或刷新后可从本机存储恢复再传 ----
async function refreshSaved() {
  const info = recordingStore.supported ? await recordingStore.meta() : undefined
  saved.value = info && info.samples > RATE ? info : null
}
async function settle(ok: boolean, draftStartedAt: number) {
  if (ok) await recordingStore.clear(draftStartedAt)
  if (mounted) await refreshSaved()
}
async function restore() {
  const info = saved.value
  if (!info || props.busy) return
  const parts = await recordingStore.chunks()
  if (!mounted || props.busy || saved.value !== info) return
  const total = (parts ?? []).reduce((s, c) => s + c.length, 0)
  if (!parts || !total) { error.value = '本机保存的录音已无法读取'; await settle(true, info.startedAt); return }
  emit('recorded', { ownerId, startedAt: info.startedAt, file: new File([wav(parts, total)], fileName(info.startedAt), { type: 'audio/wav' }), marks: info.marks, topic: info.topic, participants: info.participants })
}
async function dropSaved() {
  const info = saved.value
  if (!info || props.busy) return
  try { await ElMessageBox.confirm('放弃后这段录音无法找回。', '放弃未上传的录音', { type: 'warning', confirmButtonText: '放弃', cancelButtonText: '保留' }) } catch { return }
  if (mounted && !props.busy) await settle(true, info.startedAt)
}
// 录音中整页只留录音，入口页隐藏标题、上传行与最近的会议
const open = computed(() => state.value !== 'idle')
defineExpose({ settle, open })
onMounted(async () => { await refreshSaved(); checkingSaved.value = false })
onBeforeUnmount(() => {
  mounted = false
  clearTimeout(metaTimer)
  if (live.value) { flush(); void recordingStore.saveMeta(meta()) }
  closeMic()
  window.removeEventListener('beforeunload', guard)
})
</script>

<template>
  <div class="recorder">
    <div v-if="saved && state === 'idle'" class="saved" role="status">
      <RotateCcw :size="16" :stroke-width="1.75" />
      <span class="saved-text">发现一段未上传的录音：<span class="num">{{ savedText }}</span><template v-if="saved.topic">，主题“{{ saved.topic }}”</template></span>
      <el-button size="small" type="primary" :disabled="busy" @click="restore">上传这段录音</el-button>
      <el-button size="small" text :disabled="busy" @click="dropSaved">放弃</el-button>
    </div>

    <!-- 入口：声纹带加中间的录音大按钮，上传录音合进同一张卡（插槽 drop） -->
    <div v-if="state === 'idle'" class="vband">
      <VoiceBand />
      <button type="button" class="vb-rec" :disabled="!!saved || busy || checkingSaved || opening" aria-label="开始现场录音" title="开始现场录音" @click="begin"><Mic :size="30" :stroke-width="1.75" /></button>
      <slot name="drop" />
    </div>

    <!-- 录音中：大号计时在上，实时声波，暂停、结束并转写 -->
    <div v-else class="live" role="status" :aria-label="`正在录音 ${elapsed}`">
      <div class="rec-head">
        <SiBadge :tone="state === 'paused' ? 'neutral' : 'danger'" dot>{{ state === 'paused' ? '已暂停' : '正在录音' }}</SiBadge>
        <div class="rt2 num">{{ elapsed }}</div>
        <p class="hint">录音保存在本机浏览器，断网也不会丢（每 2 秒保存一次，刷新后可恢复）。</p>
      </div>
      <div class="vband rec"><VoiceBand :levels="bars" :paused="state === 'paused'" /></div>
      <div class="actions">
        <el-button size="large" @click="pause"><component :is="state === 'paused' ? Play : Pause" :size="14" :stroke-width="1.75" /><span>{{ state === 'paused' ? '继续' : '暂停' }}</span></el-button>
        <el-button size="large" @click="mark"><Flag :size="14" :stroke-width="1.75" /><span>打点</span></el-button>
        <el-button size="large" type="primary" @click="stop"><Square :size="12" :stroke-width="2" /><span>结束并转写</span></el-button>
        <el-button size="large" text @click="discard"><Trash2 :size="14" :stroke-width="1.75" /><span>放弃</span></el-button>
      </div>
      <div class="fields">
        <div v-if="marks.length" class="marks"><span v-for="(m, i) in marks" :key="i" class="mark num"><Flag :size="11" :stroke-width="1.75" />{{ stamp(m) }}<button type="button" :aria-label="`删除标记 ${stamp(m)}`" @click="unmark(i)"><X :size="11" :stroke-width="2" /></button></span></div>
        <label class="row"><span class="label">会议主题</span><el-input v-model="topic" maxlength="40" placeholder="可不填，之后可改" @input="metaChanged" /></label>
        <label class="row"><span class="label">参会人</span>
          <el-select v-model="participants" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入姓名后回车，可不填" aria-label="参会人" @change="cleanNames" />
        </label>
      </div>
    </div>

    <p v-if="error === 'https'" class="error">浏览器只在 https 地址下允许使用麦克风。请改用 <a :href="httpsUrl">{{ httpsUrl }}</a> 打开本系统后重试。</p>
    <p v-else-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
/* 澄明二版 6.12：声纹带卡；录音中整页居中 */
.recorder { display: flex; flex-direction: column; gap: 12px; }
.recorder :deep(.el-button > span) { display: inline-flex; align-items: center; gap: 6px; }
.vband { position: relative; padding: 34px 36px 26px; overflow: hidden; border: 1px solid var(--si-hair); border-radius: 22px; background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.vband.rec { padding: 24px 36px; }
.vb-rec { position: absolute; left: 50%; top: 56px; display: grid; place-items: center; width: 76px; height: 76px; margin-left: -38px; padding: 0; border: 0; border-radius: 50%; background: var(--si-m-meet); color: var(--si-white); cursor: pointer; box-shadow: 0 0 0 10px var(--si-surface-1), 0 0 0 11px color-mix(in srgb, var(--si-m-meet) 30%, transparent), 0 14px 30px -8px color-mix(in srgb, var(--si-m-meet) 60%, transparent); transition: transform var(--si-d3) var(--si-ease-spring); }
.vb-rec:hover:not(:disabled) { transform: scale(1.06); }
.vb-rec:active:not(:disabled) { transform: scale(.97); }
.vb-rec:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 14px; }
.vb-rec:disabled { opacity: .5; cursor: not-allowed; }
.saved { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 12px; border: 1px solid var(--si-hair-strong); border-radius: var(--si-radius-md); background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-caption); }
.saved-text { flex: 1; min-width: 160px; }
.live { display: flex; flex-direction: column; gap: 4px; }
.rec-head { margin-bottom: 18px; text-align: center; }
.rt2 { margin-top: 12px; color: var(--si-ink-1); font: var(--si-font-title-lg); font-size: calc(52px * var(--si-fs)); line-height: 1.15; letter-spacing: .02em; }
.rec-head .hint { margin: 6px 0 0; }
.row { display: flex; align-items: center; gap: 10px; }
.label { flex: none; width: 64px; color: var(--si-ink-3); font: var(--si-font-caption); }
.row :deep(.el-select), .row :deep(.el-input) { flex: 1; min-width: 0; }
.actions { display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.actions :deep(.el-button) { margin: 0; }
.fields { display: flex; flex-direction: column; gap: 12px; width: min(520px, 100%); margin: 20px auto 0; }
.marks { display: flex; flex-wrap: wrap; gap: 6px; }
.mark { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: var(--si-radius-badge); background: var(--si-m-meet-soft); color: var(--si-ink-2); font: var(--si-font-caption); }
.mark button { display: inline-flex; padding: 0; border: 0; background: none; color: var(--si-ink-3); cursor: pointer; }
.hint { margin: 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.error { margin: 0; text-align: center; color: var(--si-danger-ink); font: var(--si-font-meta); }
.error a { color: inherit; text-decoration: underline; }
</style>
