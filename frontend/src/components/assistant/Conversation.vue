<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import type { Message, Template } from '@/api/assistant'
import MessageItem from './MessageItem.vue'
import TemplatePicker from './TemplatePicker.vue'
const props = defineProps<{ messages: Message[]; sessionId: string; template: Template | null }>()
const emit = defineEmits<{ stop: [mid: number]; retry: [mid: number]; template: [value: Template | null] }>()
const area = ref<HTMLElement>()
onMounted(async () => { await nextTick(); if (props.messages.length && area.value) area.value.scrollTop = area.value.scrollHeight })
watch(() => props.messages.map(m => m.content).join(''), async () => {
  const follow = area.value && area.value.scrollHeight - area.value.scrollTop - area.value.clientHeight < 160
  await nextTick()
  if (follow && area.value) area.value.scrollTop = area.value.scrollHeight
})
</script>
<template>
  <div ref="area" class="conversation">
    <div class="reading">
      <template v-if="!messages.length"><p class="empty-hint">加入文件并确认脱敏后提问，或选择一个模板开始</p><TemplatePicker :cards="true" :model-value="template" @update:model-value="emit('template', $event)" /></template>
      <MessageItem v-for="message in messages" :key="message.id" :message="message" :session-id="sessionId" @stop="emit('stop', $event)" @retry="emit('retry', $event)" />
    </div>
  </div>
</template>
<style scoped>
.conversation { flex: 1; min-height: 0; overflow-y: auto; padding: 0 20px 20px; }
.reading { max-width: 800px; margin: 0 auto; }
.empty-hint { margin: 24px 0; color: var(--el-text-color-secondary); font: var(--si-font-body); }
</style>
