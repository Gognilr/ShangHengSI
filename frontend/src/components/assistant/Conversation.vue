<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { ArrowDown } from '@lucide/vue'
import type { Message } from '@/api/assistant'
import MessageItem from './MessageItem.vue'
const props = defineProps<{ messages: Message[]; sessionId: string }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number] }>()
const area = ref<HTMLElement>()
// 自动跟随：离底部 80px 以内视为在底部；用户向上滚动即停止跟随，点“回到底部”恢复
const following = ref(true)
function toBottom() { if (area.value) area.value.scrollTop = area.value.scrollHeight; following.value = true }
function onScroll() { const el = area.value; if (el) following.value = el.scrollHeight - el.scrollTop - el.clientHeight <= 80 }
onMounted(async () => { await nextTick(); toBottom() })
// 切换会话、首次载入消息时滚到底部
watch(() => [props.sessionId, props.messages.length === 0] as const, async () => { await nextTick(); toBottom() })
watch(() => props.messages.map(m => `${m.id}:${m.content.length}`).join(','), async () => {
  if (!following.value) return
  await nextTick(); toBottom()
})
const lastReply = () => [...props.messages].reverse().find(m => m.role === 'assistant')?.id
</script>
<template>
  <div class="conversation-wrap">
    <div ref="area" class="conversation" @scroll.passive="onScroll">
      <div class="reading">
        <p v-if="!messages.length" class="empty-hint">可以直接提问；文件加入后在本机解析与脱敏，发给模型的只有脱敏后的文字。</p>
        <MessageItem v-for="message in messages" :key="message.id" :message="message" :session-id="sessionId" :last="message.id === lastReply()" @stop="emit('stop', $event)" @retry="emit('retry', $event)" />
      </div>
    </div>
    <el-button v-if="!following" class="to-bottom" round size="small" @click="toBottom"><template #icon><ArrowDown :size="16" :stroke-width="1.75" /></template>回到底部</el-button>
  </div>
</template>
<style scoped>
.conversation-wrap { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.conversation { flex: 1; min-height: 0; overflow-y: auto; padding: 0 24px 24px; }
.reading { max-width: 760px; margin: 0 auto; }
.empty-hint { margin: 32px 0; text-align: center; color: var(--el-text-color-secondary); font: var(--si-font-body); }
.to-bottom { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%); box-shadow: var(--el-box-shadow-light); }
</style>
