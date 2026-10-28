<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { assistantApi, type Message } from '@/api/assistant'
import type { PreviewNode } from '@/api/preview'
import { ApiError } from '@/api/http'
const props = defineProps<{ message: Message; sessionId: string }>()
const variant = ref('translation'), originals = ref<PreviewNode[]>([]), error = ref(''), loading = ref(false)
let active = true
onBeforeUnmount(() => { active = false })
watch(variant, async value => {
  if (value !== 'bilingual' || originals.value.length) return
  loading.value = true
  try {
    const ids = props.message.params.file_ids
    const results = await Promise.all((Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []).map(id => assistantApi.nodes(props.sessionId, id)))
    if (active) originals.value = results.flatMap(result => result.items.filter(node => node.part === 'body'))
  } catch (e) { if (active) error.value = e instanceof ApiError ? e.message : '原文加载失败，请重试' }
  finally { if (active) loading.value = false }
})
</script>
<template>
  <div>
    <p class="hint">名称、地址、金额按原文写法还原，请核对</p>
    <el-radio-group v-model="variant" size="small"><el-radio-button value="translation">译文</el-radio-button><el-radio-button value="bilingual">对照</el-radio-button></el-radio-group>
    <p v-if="message.extras.segments_total" class="hint num">第 {{ message.extras.segments_done ?? 0 }} / {{ message.extras.segments_total }} 段</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-if="message.extras.translation_unaligned?.length" title="部分译文未能按位置对齐，请核对原文" type="warning" :closable="false" />
    <div v-if="variant === 'bilingual'" v-loading="loading" class="bilingual">
      <div v-for="node in originals" :key="node.loc" class="translation-row"><p>{{ node.text ?? '原文已按保留期限删除' }}</p><p>{{ message.translation?.[node.loc] ?? '未完成翻译' }}</p></div>
    </div>
    <pre v-else>{{ message.content }}</pre>
  </div>
</template>
<style scoped>
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
.bilingual { margin-top: 16px; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.translation-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-bottom: 1px solid var(--el-border-color-light); }
.translation-row:last-child { border-bottom: 0; }
.translation-row p { padding: 12px; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.translation-row p + p { border-left: 1px solid var(--el-border-color-light); }
</style>
