<script setup lang="ts">
// 浏览器内录音（V31-U5 第四节、V31-U5F-05 第 5.1 节）：16 kHz 单声道 PCM。
// 开始前选麦克风并试音；录音中显示波形、计时，可暂停、打点、填主题与参会人；
// 分片实时写入浏览器本地存储（IndexedDB），刷新后可恢复；不做实时转写。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Flag, Mic, Pause, Play, RotateCcw, Square, Trash2, X } from '@lucide/vue'
import { recordingStoreFor, type RecordingMeta } from '@/utils/recordingStore'
import { stamp } from '@/utils/transcript'

export interface Recorded { ownerId: string; startedAt: number; file: File; marks: number[]; topic: string; participants: string[] }
const props = defineProps<{ ownerId: string; busy: boolean }>()
const ownerId = props.ownerId
const recordingStore = recordingStoreFor(ownerId)
const emit = defineEmits<{ recorded: [value: Recorded] }>()
const RATE = 16000, LIMIT_S = 3 * 3600, FLUSH = RATE * 2, BARS = 48
type State = 'idle' | 'setup' | 'recording' | 'paused'
const state = ref<State>('idle'), seconds = ref(0), level = ref(0), error = ref('')
const bars = ref<number[]>(Array(BARS).fill(0))
const devices = ref<MediaDeviceInfo[]>([]), deviceId = ref(''), testing = ref(false)
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
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return '没有找到麦克风：请连接麦克风，或在下方换一个设备后重试'
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

// ---- 麦克风与试音 ----
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
  } else if (state.value === 'setup') bars.value = [...bars.value.slice(1), level.value]
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
  const inputStream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: deviceId.value ? { exact: deviceId.value } : undefined, channelCount: 1, echoCancellation: false, noiseSuppression: false } })
  if (request !== micRequest) { inputStream.getTracks().forEach(t => t.stop()); return }
  stream = inputStream
  ctx = new AudioContext({ sampleRate: RATE })
  const source = ctx.createMediaStreamSource(stream)
  node = ctx.createScriptProcessor(4096, 1, 1)
  node.onaudioprocess = process
  source.connect(node); node.connect(ctx.destination)
  // 授权后才能读到设备名称
  const inputs = await navigator.mediaDevices.enumerateDevices()
  if (request !== micRequest) return
  devices.value = inputs.filter(d => d.kind === 'audioinput')
  const current = inputStream.getAudioTracks()[0]?.getSettings().deviceId
  if (!deviceId.value && current) deviceId.value = current
}
function closeMic() {
  micRequest++
  node?.disconnect(); node = null
  stream?.getTracks().forEach(t => t.stop()); stream = null
  void ctx?.close(); ctx = null
  level.value = 0
}
async function openSetup() {
  if (props.busy || checkingSaved.value || saved.value) return
  error.value = ''
  if (!secure) { error.value = 'https'; return }
  if (!supported) { error.value = '当前浏览器不支持录音，请使用最新版 Edge 或 Chrome'; return }
  testing.value = true
  try { state.value = 'setup'; bars.value = Array(BARS).fill(0); await openMic() }
  catch (e) { error.value = describe(e); state.value = 'idle'; closeMic() }
  finally { testing.value = false }
}
async function changeDevice() {
  try { await openMic() } catch (e) { error.value = describe(e); state.value = 'idle'; closeMic() }
}
function cancelSetup() { closeMic(); state.value = 'idle'; error.value = '' }

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
// 录音卡展开（设置或录音中）时，入口页隐藏“上传录音”卡
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
  <div class="recorder" :class="{ open: state !== 'idle' }">
    <div v-if="saved && state === 'idle'" class="saved" role="status">
      <RotateCcw :size="16" :stroke-width="1.75" />
      <span class="saved-text">发现一段未上传的录音：<span class="num">{{ savedText }}</span><template v-if="saved.topic">，主题“{{ saved.topic }}”</template></span>
      <el-button size="small" type="primary" :disabled="busy" @click="restore">上传这段录音</el-button>
      <el-button size="small" text :disabled="busy" @click="dropSaved">放弃</el-button>
    </div>

    <div v-if="state === 'idle'" class="idle">
      <button type="button" class="rec-btn" :disabled="!!saved || busy || checkingSaved" aria-label="开始现场录音" @click="openSetup"><Mic :size="30" :stroke-width="1.75" /></button>
      <b class="idle-title">现场录音</b>
      <span class="hint">{{ saved ? '请先处理上面未上传的录音' : '用这台电脑的麦克风录会议，录完自动转写' }}</span>
    </div>

    <div v-else-if="state === 'setup'" v-loading="testing" class="panel" role="group" aria-label="录音设置">
      <label class="row"><span class="label">麦克风</span>
        <el-select v-model="deviceId" aria-label="选择麦克风" @change="changeDevice"><el-option v-for="(d, i) in devices" :key="d.deviceId" :value="d.deviceId" :label="d.label || `麦克风 ${i + 1}`" /></el-select>
      </label>
      <div class="row"><span class="label">试音</span>
        <div class="test"><div class="wave" aria-hidden="true"><i v-for="(b, i) in bars" :key="i" :style="{ '--h': Math.max(0.06, b) }" /></div>
          <span class="hint">对着麦克风说话，波形跳动说明正常</span></div>
      </div>
      <label class="row"><span class="label">会议主题</span><el-input v-model="topic" maxlength="40" placeholder="可不填，之后可改" /></label>
      <label class="row"><span class="label">参会人</span>
        <el-select v-model="participants" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入姓名后回车，可不填" aria-label="参会人" @change="cleanNames" />
      </label>
      <div class="actions"><el-button text @click="cancelSetup">取消</el-button><el-button type="primary" @click="start"><span class="rec-dot" aria-hidden="true" /><span>开始录音</span></el-button></div>
    </div>

    <div v-else class="panel live" role="status" :aria-label="`正在录音 ${elapsed}`">
      <div class="head">
        <span class="dot" :class="{ paused: state === 'paused' }" aria-hidden="true" />
        <span class="num time">{{ elapsed }}</span>
        <span class="state">{{ state === 'paused' ? '已暂停' : '录音中' }}</span>
      </div>
      <div class="wave big" aria-hidden="true"><i v-for="(b, i) in bars" :key="i" :style="{ '--h': state === 'paused' ? 0.06 : Math.max(0.06, b) }" /></div>
      <p class="hint safe">录音保存在本机浏览器，断网也不会丢（每 2 秒保存一次，刷新后可恢复）。</p>
      <div class="actions center">
        <el-button @click="pause"><component :is="state === 'paused' ? Play : Pause" :size="14" :stroke-width="1.75" /><span>{{ state === 'paused' ? '继续' : '暂停' }}</span></el-button>
        <el-button @click="mark"><Flag :size="14" :stroke-width="1.75" /><span>打点</span></el-button>
        <el-button type="primary" @click="stop"><Square :size="12" :stroke-width="2" /><span>结束并转写</span></el-button>
        <el-button text @click="discard"><Trash2 :size="14" :stroke-width="1.75" /><span>放弃</span></el-button>
      </div>
      <div v-if="marks.length" class="marks"><span v-for="(m, i) in marks" :key="i" class="mark num"><Flag :size="11" :stroke-width="1.75" />{{ stamp(m) }}<button type="button" :aria-label="`删除标记 ${stamp(m)}`" @click="unmark(i)"><X :size="11" :stroke-width="2" /></button></span></div>
      <label class="row"><span class="label">会议主题</span><el-input v-model="topic" maxlength="40" placeholder="可不填，之后可改" @input="metaChanged" /></label>
      <label class="row"><span class="label">参会人</span>
        <el-select v-model="participants" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入姓名后回车，可不填" aria-label="参会人" @change="cleanNames" />
      </label>
    </div>

    <p v-if="error === 'https'" class="error">浏览器只在 https 地址下允许使用麦克风。请改用 <a :href="httpsUrl">{{ httpsUrl }}</a> 打开本系统后重试。</p>
    <p v-else-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
/* 澄明 8.6：入口页左边的玫红大卡；录音中展开为整行 */
.recorder { display: flex; flex-direction: column; align-items: stretch; justify-content: center; gap: 12px; min-height: 240px; padding: 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); box-sizing: border-box; }
.recorder.open { grid-column: 1 / -1; align-items: center; }
.recorder :deep(.el-button > span) { display: inline-flex; align-items: center; gap: 6px; }
.idle { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; }
.idle-title { color: var(--si-ink-1); font: var(--si-font-h3); }
.rec-btn { display: grid; place-items: center; width: 76px; height: 76px; margin-bottom: 4px; border: 0; border-radius: 50%; background: var(--si-m-meet); color: var(--si-white); cursor: pointer; box-shadow: 0 0 0 8px var(--si-m-meet-soft); transition: transform var(--si-d3) var(--si-ease-spring), box-shadow var(--si-d3) var(--si-ease-out); }
.rec-btn:hover:not(:disabled) { transform: scale(1.06); box-shadow: 0 0 0 12px var(--si-m-meet-soft); }
.rec-btn:active:not(:disabled) { transform: scale(.97); }
.rec-btn:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 4px; }
.rec-btn:disabled { opacity: .5; cursor: not-allowed; }
.saved { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; align-self: stretch; padding: 8px 12px; border: 1px solid var(--si-hair-strong); border-radius: var(--si-radius-md); background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-caption); }
.saved-text { flex: 1; min-width: 160px; }
.panel { width: min(520px, 100%); display: flex; flex-direction: column; gap: 14px; }
.row { display: flex; align-items: center; gap: 10px; }
.label { flex: none; width: 64px; color: var(--si-ink-3); font: var(--si-font-caption); }
.row :deep(.el-select), .row :deep(.el-input), .test { flex: 1; min-width: 0; }
.test { display: flex; flex-direction: column; gap: 4px; }
.wave { display: flex; align-items: center; gap: 2px; height: 32px; }
.wave.big { height: 64px; }
.wave i { flex: 1; min-width: 2px; height: calc(100% * var(--h)); border-radius: 2px; background: var(--si-m-meet); transition: height 120ms linear; }
.head { display: flex; align-items: center; justify-content: center; gap: 10px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--si-m-meet); animation: blink 1.2s var(--si-ease-io) infinite; }
.dot.paused { background: var(--si-ink-4); animation: none; }
@keyframes blink { 50% { opacity: .3; } }
.time { font: var(--si-font-title-lg); font-size: calc(40px * var(--si-fs)); line-height: 1.1; color: var(--si-ink-1); }
.state { color: var(--si-ink-3); font: var(--si-font-caption); }
.safe { text-align: center; }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.actions.center { justify-content: center; }
.actions :deep(.el-button) { margin: 0; }
.rec-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--si-m-meet); }
.marks { display: flex; flex-wrap: wrap; gap: 6px; }
.mark { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: var(--si-radius-badge); background: var(--si-m-meet-soft); color: var(--si-ink-2); font: var(--si-font-caption); }
.mark button { display: inline-flex; padding: 0; border: 0; background: none; color: var(--si-ink-3); cursor: pointer; }
.hint { margin: 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.error { margin: 0; text-align: center; color: var(--si-danger-ink); font: var(--si-font-meta); }
.error a { color: inherit; text-decoration: underline; }
</style>
