<script setup lang="ts">
import { ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { runsApi, type Finding } from '@/api/runs'
import type { Message } from '@/api/assistant'

/**
 * 讨论稿保存为该问题的修改建议（V32-B-03）。
 * 从回复中取【修改条文】…【/修改条文】内的文字（已还原）给用户核对、编辑；保存时服务器在本地脱敏后存代号文本，
 * 原审核建议不变，保存后默认选用讨论稿。没有标记时请用户让助手重新生成，或在这里手工整理。
 * “保存并采纳”（V321-E-02）：保存后再把处理状态设为已采纳；结果回传给结果页就地更新，不弹全局提示。
 */
const props = defineProps<{ anchor: { id: string; no: string; title: string } | null; message: Message | null }>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ saved: [finding: Finding] }>()
const text = ref(''), found = ref(true), saving = ref(false), error = ref('')

const MARKED = /【修改条文】([\s\S]*?)【\/修改条文】/g
function extract(content: string): string {
  return [...content.matchAll(MARKED)].map(m => m[1]!.trim()).filter(Boolean).join('\n')
}
watch(() => [open.value, props.message?.id] as const, ([visible]) => {
  if (!visible || !props.message) return
  text.value = extract(props.message.content); found.value = !!text.value; error.value = ''
}, { immediate: true })

async function save(adopt: boolean) {
  if (!props.anchor || !props.message || !text.value.trim() || saving.value) return
  saving.value = true; error.value = ''
  const findingId = props.anchor.id
  try {
    let finding = await runsApi.saveDraft(findingId, { text: text.value, message_id: props.message.id })
    emit('saved', finding)
    if (adopt && finding.handling_status !== 'adopted') {
      try { finding = await runsApi.patchFinding(findingId, { handling_status: 'adopted' }); emit('saved', finding) }
      catch (e) { error.value = `已保存，采纳未成功：${e instanceof ApiError ? e.message : '请在问题卡上重试'}`; return }
    }
    open.value = false
  } catch (e) { error.value = e instanceof ApiError ? e.message : '保存失败，请重试' }
  finally { saving.value = false }
}
</script>

<template>
  <el-dialog v-model="open" title="保存到该问题的修改建议" width="560px" append-to-body>
    <p v-if="anchor" class="target">问题 {{ anchor.no }}「{{ anchor.title }}」</p>
    <el-alert v-if="!found" type="warning" :closable="false" show-icon
              title="这条回复里没有可提取的修改条文。可以让助手“给出完整的替换条文”后再保存，或在下面手工整理。" />
    <el-input v-model="text" type="textarea" :autosize="{ minRows: 4, maxRows: 12 }" maxlength="4000" show-word-limit
              placeholder="可直接替换原文的条文" aria-label="修改条文" />
    <p class="hint">保存后在本机脱敏再存储，原审核建议保留不变；采纳和导出批注版默认使用这份讨论修改稿，可在问题卡上改回原审核建议。</p>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button :disabled="!text.trim() || saving" @click="save(false)">只保存</el-button>
      <el-button type="primary" :loading="saving" :disabled="!text.trim()" @click="save(true)">保存并采纳</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.target { margin: 0 0 12px; font: var(--si-font-h4); color: var(--si-ink-1); overflow-wrap: anywhere; }
.el-alert { margin-bottom: 12px; }
.hint { margin: 8px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
</style>
