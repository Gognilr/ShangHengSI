<script setup lang="ts">
// 译稿（澄明 8.7）：原文与译文放在同一张纸面上按段成行，天然对齐、一起滚动；
// 悬停的段落整行高亮；正在生成的段落末尾有光标，未生成的显示骨架
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { assistantApi, type Message } from '@/api/assistant'
import type { PreviewNode } from '@/api/preview'
import { ApiError } from '@/api/http'
const props = defineProps<{ message: Message; sessionId: string }>()
const originals = ref<PreviewNode[]>([]), error = ref(''), loading = ref(true)
// 没有对照行可显示时退回纯译文；译文视图不显示行首的分段标记 ⟦F1-P1⟧（只用于对齐，V321-Z）
const plain = computed(() => props.message.content.replace(/^⟦[^⟧\r\n]+⟧[ \t]*/gm, ''))
const pending = computed(() => ['queued', 'streaming'].includes(props.message.status))
const unaligned = computed(() => props.message.extras.translation_unaligned?.length ?? 0)
const firstMissing = computed(() => pending.value ? originals.value.findIndex(n => !props.message.translation?.[n.loc]) : -1)
let active = true
onBeforeUnmount(() => { active = false })
onMounted(async () => {
  try {
    const ids = props.message.params.file_ids ?? (props.message.params.file_id ? [props.message.params.file_id] : [])
    const results = await Promise.all((Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []).map(id => assistantApi.nodes(props.sessionId, id)))
    if (active) originals.value = results.flatMap(result => result.items.filter(node => node.part === 'body'))
  } catch (e) { if (active) error.value = e instanceof ApiError ? e.message : '原文加载失败，请重试' }
  finally { if (active) loading.value = false }
})
</script>
<template>
  <div class="tv">
    <p class="notice">{{ message.translation_notice ?? '译稿请逐段核对名称、金额和业务条件；名称、地址、金额按原文写法还原' }}</p>
    <p v-if="message.extras.segments_total" class="prog num">第 {{ message.extras.segments_done ?? 0 }} / {{ message.extras.segments_total }} 段</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert v-if="unaligned" title="部分译文未能按位置对齐，请核对原文" type="warning" :closable="false" />
    <div v-if="originals.length" class="paper">
      <div class="cols" aria-hidden="true"><span>原文</span><span>译文</span></div>
      <div v-for="(node, i) in originals" :key="node.loc" class="row" :class="{ live: i === firstMissing }">
        <p>{{ node.text ?? '原文已按保留期限删除' }}</p>
        <p v-if="message.translation?.[node.loc]">{{ message.translation[node.loc] }}<i v-if="pending && i === firstMissing - 1" class="cursor" aria-hidden="true" /></p>
        <p v-else-if="pending" class="skel" aria-hidden="true"><i /><i /><i class="short" /><i v-if="i === firstMissing" class="cursor" /></p>
        <p v-else class="miss">未完成翻译</p>
      </div>
    </div>
    <pre v-else-if="!loading" class="plain">{{ plain }}</pre>
  </div>
</template>
<style scoped>
.tv { display: flex; flex-direction: column; gap: 10px; }
.notice { margin: 0; padding: 8px 14px; border-radius: 10px; background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-caption); }
.prog { margin: 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.paper { border: 1px solid var(--si-hair); border-radius: 16px; background: var(--si-paper); box-shadow: var(--si-paper-shadow); overflow: hidden; }
.cols, .row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.cols span { padding: 8px 20px; border-bottom: 1px solid var(--si-hair-faint); background: var(--si-surface-2); color: var(--si-ink-3); font: var(--si-font-label-sm); }
.cols span + span { border-left: 1px solid var(--si-hair-faint); }
.row { border-bottom: 1px solid var(--si-hair-faint); transition: background-color var(--si-d2) var(--si-ease-out); }
.row:last-child { border-bottom: 0; }
.row:hover { background: var(--si-blue-tint); }
.row p { margin: 0; padding: 12px 20px; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--si-paper-ink-2); font: var(--si-font-reading); }
.row p + p { border-left: 1px solid var(--si-hair-faint); }
.miss { color: var(--si-ink-4) !important; }
.skel { display: flex; flex-direction: column; gap: 8px; }
.skel i:not(.cursor) { display: block; height: 10px; border-radius: 5px; background: linear-gradient(90deg, var(--si-surface-2) 25%, var(--si-hair-faint) 50%, var(--si-surface-2) 75%) 0 0 / 200% 100%; animation: tv-shim 1.4s linear infinite; }
.skel i.short { width: 55%; }
@keyframes tv-shim { to { background-position: -200% 0; } }
html:is([data-fx="lite"], [data-fx="off"]) .skel i { animation: none; }
.cursor { display: inline-block; width: 2px; height: 1em; margin-left: 2px; vertical-align: -2px; background: var(--si-ai); animation: tv-blink 1s steps(2) infinite; }
@keyframes tv-blink { 50% { opacity: 0; } }
.plain { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
@media (max-width: 900px) { .cols, .row { grid-template-columns: 1fr; } .row p + p { border-left: 0; } .cols span:last-child { display: none; } }
</style>
