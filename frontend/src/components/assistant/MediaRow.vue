<script setup lang="ts">
import { computed } from 'vue'
import type { SessionFile } from '@/api/assistant'
import { useLoop } from '@/utils/motion'
const props = defineProps<{ file: SessionFile; busy: boolean }>()
const emit = defineEmits<{ transcript: []; download: []; retry: [] }>()
const media = computed(() => props.file.media!)
const loop = useLoop(computed(() => media.value.stage === 'transcribing'))
function stamp(ms = 0) { const s = Math.floor(ms / 1000); return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(v => String(v).padStart(2, '0')).join(':') }
const progress = computed(() => media.value.duration_ms ? Math.min(100, Math.max(0, media.value.progress / media.value.duration_ms * 100)) : 0)
const failure = computed(() => ({ unavailable: '转写服务暂不可用，可稍后重试', timeout: '转写超时', transcribe_failed: '转写失败', decode_failed: '无法解码该录音' })[media.value.error ?? 'transcribe_failed'])
</script>
<template>
  <div class="media-row">
    <span v-if="media.duration_ms" class="hint num">{{ stamp(media.duration_ms) }}</span>
    <p v-if="media.stage === 'decoding'">正在本机解码</p>
    <p v-else-if="media.stage === 'queued'">{{ media.queue_position ? `排队转写，前面还有 ${media.queue_position} 个` : '即将开始转写' }}</p>
    <p v-else-if="media.stage === 'waiting_memory'">等待服务器空闲内存</p>
    <template v-else-if="media.stage === 'transcribing'"><el-progress :percentage="progress" :show-text="false" /><p class="num">本机转写中 {{ stamp(media.progress) }} / {{ stamp(media.duration_ms) }}</p><div class="scan" :class="{ running: loop }" /></template>
    <template v-else-if="media.stage === 'done'"><p>{{ file.confirmed_at ? '已确认' : '转写完成，请校对' }}</p><el-button :type="file.confirmed_at ? 'default' : 'primary'" :link="!!file.confirmed_at" size="small" @click="emit('transcript')">{{ file.confirmed_at ? '查看逐字稿' : '校对逐字稿' }}</el-button><el-button link :disabled="busy" @click="emit('download')">逐字稿 Word</el-button></template>
    <template v-else-if="media.stage === 'failed'"><p class="error">{{ failure }}</p><el-button v-if="media.error !== 'decode_failed'" link :disabled="busy" @click="emit('retry')">重新转写</el-button></template>
  </div>
</template>
<style scoped>
.media-row { position: relative; padding-bottom: 4px; font: var(--si-font-caption); }
p { margin: 4px 0; }
.error { color: var(--si-danger-ink); }
.scan { height: 2px; background: var(--si-hair); overflow: hidden; }
.scan.running::after { content: ''; display: block; height: 100%; width: 30%; background: var(--si-blue); animation: scan 2s linear infinite; }
@keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
</style>
