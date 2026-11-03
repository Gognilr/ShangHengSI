<script setup lang="ts">
// 浏览器内录音（V31-U5 第四节）：16 kHz 单声道 PCM 只在内存中，停止后生成 WAV 交给页面上传；不写浏览器存储
import { computed, onBeforeUnmount, ref } from 'vue'
import { Mic, Pause, Play, Square, Trash2 } from '@lucide/vue'

const emit = defineEmits<{ recorded: [file: File] }>()
const RATE = 16000, LIMIT_S = 3 * 3600
const state = ref<'idle' | 'recording' | 'paused'>('idle'), seconds = ref(0), level = ref(0), error = ref('')
let ctx: AudioContext | null = null, stream: MediaStream | null = null, node: ScriptProcessorNode | null = null
let chunks: Int16Array[] = [], samples = 0
const supported = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
const elapsed = computed(() => { const s = Math.floor(seconds.value); return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(v => String(v).padStart(2, '0')).join(':') })

function guard(event: BeforeUnloadEvent) { event.preventDefault() }
async function start() {
  error.value = ''
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false } })
    ctx = new AudioContext({ sampleRate: RATE })
    const source = ctx.createMediaStreamSource(stream)
    node = ctx.createScriptProcessor(4096, 1, 1)
    node.onaudioprocess = event => {
      if (state.value !== 'recording') return
      const input = event.inputBuffer.getChannelData(0), out = new Int16Array(input.length)
      let sum = 0
      for (let i = 0; i < input.length; i++) { const v = Math.max(-1, Math.min(1, input[i]!)); out[i] = v < 0 ? v * 0x8000 : v * 0x7fff; sum += v * v }
      chunks.push(out); samples += out.length; seconds.value = samples / RATE
      level.value = Math.min(1, Math.sqrt(sum / input.length) * 4)
      if (seconds.value >= LIMIT_S) void stop()
    }
    source.connect(node); node.connect(ctx.destination)
    chunks = []; samples = 0; seconds.value = 0; state.value = 'recording'
    window.addEventListener('beforeunload', guard)
  } catch {
    error.value = '无法使用麦克风：请在浏览器地址栏允许麦克风权限后重试'
    cleanup()
  }
}
function pause() { state.value = state.value === 'recording' ? 'paused' : 'recording' }
function wav(): Blob {
  const header = new DataView(new ArrayBuffer(44)), bytes = samples * 2
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) header.setUint8(offset + i, value.charCodeAt(i)) }
  text(0, 'RIFF'); header.setUint32(4, 36 + bytes, true); text(8, 'WAVE'); text(12, 'fmt ')
  header.setUint32(16, 16, true); header.setUint16(20, 1, true); header.setUint16(22, 1, true)
  header.setUint32(24, RATE, true); header.setUint32(28, RATE * 2, true); header.setUint16(32, 2, true); header.setUint16(34, 16, true)
  text(36, 'data'); header.setUint32(40, bytes, true)
  return new Blob([header.buffer, ...chunks.map(c => c.buffer as ArrayBuffer)], { type: 'audio/wav' })
}
async function stop() {
  if (state.value === 'idle') return
  const blob = samples ? wav() : null
  cleanup()
  if (blob) {
    const now = new Date(), pad = (n: number) => String(n).padStart(2, '0')
    emit('recorded', new File([blob], `现场录音_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.wav`, { type: 'audio/wav' }))
  }
}
function discard() { cleanup() }
function cleanup() {
  node?.disconnect(); node = null
  stream?.getTracks().forEach(t => t.stop()); stream = null
  void ctx?.close(); ctx = null
  chunks = []; samples = 0; level.value = 0; state.value = 'idle'
  window.removeEventListener('beforeunload', guard)
}
onBeforeUnmount(cleanup)
</script>

<template>
  <div class="recorder">
    <template v-if="state === 'idle'">
      <el-button :disabled="!supported" @click="start"><Mic :size="16" :stroke-width="1.75" /><span class="gap">现场录音</span></el-button>
      <span v-if="!supported" class="hint">需要通过 HTTPS 打开本系统才能使用麦克风</span>
    </template>
    <div v-else class="live" role="status" :aria-label="`正在录音 ${elapsed}`">
      <span class="dot" :class="{ paused: state === 'paused' }" aria-hidden="true" />
      <span class="num time">{{ elapsed }}</span>
      <span class="meter" aria-hidden="true"><span :style="{ transform: `scaleX(${level})` }" /></span>
      <el-button size="small" @click="pause"><component :is="state === 'paused' ? Play : Pause" :size="14" :stroke-width="1.75" /><span class="gap">{{ state === 'paused' ? '继续' : '暂停' }}</span></el-button>
      <el-button size="small" type="primary" @click="stop"><Square :size="12" :stroke-width="2" /><span class="gap">停止并转写</span></el-button>
      <el-button size="small" text @click="discard"><Trash2 :size="14" :stroke-width="1.75" /><span class="gap">放弃</span></el-button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.recorder { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.gap { margin-left: 6px; }
.live { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border: 1px solid var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--si-danger); animation: blink 1.2s ease-in-out infinite; }
.dot.paused { background: var(--si-ink-4); animation: none; }
@keyframes blink { 50% { opacity: .35; } }
.time { font: var(--si-font-label); min-width: 64px; }
.meter { width: 120px; height: 6px; border-radius: 3px; background: var(--si-surface-2); overflow: hidden; }
.meter span { display: block; height: 100%; background: var(--si-ok); transform-origin: left; transition: transform 80ms linear; }
.error { margin: 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
