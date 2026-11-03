<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { redactionApi, type RedactionInfo } from '@/api/redaction'
import type { TaskDetail } from '@/api/tasks'
import ConfirmCanvas, { type CanvasAnnotation } from '@/components/ConfirmCanvas.vue'

/**
 * 结果页只读画布（V31-U3 3.3）：复用确认页画布的 result 模式；替换位置与不确定项只读，读取失败时只少了胶囊，不影响问题标注。
 * 同一任务切换轮次不重新读取节点与替换，只重算标注。
 */
const props = defineProps<{
  task: TaskDetail
  note: string | null
  annotations: CanvasAnnotation[]
  localLocs: string[]
  selectedFindingId: string
  rawHighlight: string[]
  askEnabled: boolean
}>()
const emit = defineEmits<{ select: [findingId: string]; ask: [prefill: string] }>()

const info = ref<RedactionInfo>({ summary: {}, suspects: [], items: [] })
const protectedCount = computed(() => Object.values(info.value.summary).reduce((sum, n) => sum + n, 0))
watch(() => props.task.id, async (id) => {
  info.value = { summary: {}, suspects: [], items: [] }
  try {
    const res = await redactionApi.redaction(id)
    if (props.task.id === id) info.value = res
  } catch { /* 只读显示，读不到时按无替换显示原文 */ }
}, { immediate: true })

const canvas = ref<InstanceType<typeof ConfirmCanvas>>()
defineExpose({
  locate: (loc: string, findingId?: string) => canvas.value?.locate(loc, undefined, findingId),
  switchFile: (delta: 1 | -1) => canvas.value?.switchFile(delta),
  cancel: () => canvas.value?.cancel(),
})
</script>

<template>
  <ConfirmCanvas ref="canvas" mode="result" :task-id="task.id" :files="task.files" :items="info.items" :suspects="info.suspects"
    :note="note" :selected-suspect-id="null" locked :protected-count="protectedCount"
    :annotations="annotations" :local-locs="localLocs" :selected-finding-id="selectedFindingId"
    :ai-view="task.policy === 'ai'" :ask-enabled="askEnabled" :raw-highlight="rawHighlight"
    @select="emit('select', $event)" @ask="emit('ask', $event)" />
</template>
