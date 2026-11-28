<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 译稿（澄明 8.7）：原文与译文放在同一张纸面上按段成行，天然对齐、一起滚动；
// 悬停的段落整行高亮；译文按段整段到达：正在译的一段原文上有高亮循环划过（扫读），
// 译文侧只留光标和本段已用秒数；排队的段落静态显示“待译”；到达的译文淡入
import { computed, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { assistantApi, type Message } from '@/api/assistant'
import type { PreviewNode } from '@/api/preview'
import { ApiError } from '@/api/http'
import { useAppearanceStore } from '@/stores/appearance'
const props = defineProps<{ message: Message; sessionId: string }>()
const originals = ref<PreviewNode[]>([]), error = ref(''), loading = ref(true)
// 没有对照行可显示时退回纯译文；译文视图不显示行首的分段标记 ⟦F1-P1⟧（只用于对齐，V321-Z）
const plain = computed(() => props.message.content.replace(/^⟦[^⟧\r\n]+⟧[ \t]*/gm, ''))
const stopped = computed(() => props.message.status === 'stopped')
const progress = computed(() => {
  const {segments_done: done, segments_total: total} = props.message.extras
  if (stopped.value) return total && done !== undefined ? `已停止 · 完成 ${done}/${total} 段` : '已停止 · 部分译稿'
  return total ? `已译 ${done ?? 0}/${total} 段` : ''
})
const pending = computed(() => ['queued', 'streaming'].includes(props.message.status))
// 左缘刻度（二版 6.15）：已译为站色实线，生成中为光标，待译为占位色；本机翻译用青色，云端翻译用紫色
const cloud = computed(() => props.message.params.engine === 'cloud')
const rowState = (loc: string, i: number) => props.message.translation?.[loc] ? 'tk-done' : pending.value && i === firstMissing.value ? 'tk-live' : 'tk-todo'
const unaligned = computed(() => props.message.extras.translation_unaligned?.length ?? 0)
const firstMissing = computed(() => pending.value ? originals.value.findIndex(n => !props.message.translation?.[n.loc]) : -1)
// 扫读只在完整动效下运行；减弱或关闭动效时整段原文静态铺底，计时每秒走一次
const appearance = useAppearanceStore()
const sweep = computed(() => appearance.fxResolved === 'full')
const SWEEP = 6
const scan = ref(0), now = ref(Date.now())
let liveSince = Date.now()
watch(firstMissing, () => { liveSince = Date.now(); now.value = liveSince; scan.value = 0 })
watchEffect(onCleanup => {
  if (firstMissing.value < 0) return
  const moving = sweep.value
  const id = window.setInterval(() => { now.value = Date.now(); if (moving) scan.value++ }, moving ? 80 : 1000)
  onCleanup(() => window.clearInterval(id))
})
const elapsed = computed(() => Math.max(0, Math.floor((now.value - liveSince) / 1000)))
const liveParts = computed(() => {
  const chars = Array.from(originals.value[firstMissing.value]?.text ?? '')
  const end = scan.value % (chars.length + SWEEP), start = Math.max(0, end - SWEEP), stop = Math.min(end, chars.length)
  return [chars.slice(0, start).join(''), chars.slice(start, stop).join(''), chars.slice(stop).join('')]
})
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
    <SiNotice type="warning" :closable="false" :title="message.translation_notice ?? '译稿请逐段核对名称、金额和业务条件；名称、地址、金额按原文写法还原'" />
    <p v-if="progress" class="prog num" role="status">{{ progress }}</p>
    <SiNotice v-if="error" :title="error" type="error" :closable="false" />
    <SiNotice v-if="unaligned" title="部分译文未能按位置对齐，请核对原文" type="warning" :closable="false" />
    <div v-if="originals.length" class="paper">
      <div class="cols" aria-hidden="true"><span>原文</span><span>译文</span></div>
      <div v-for="(node, i) in originals" :key="node.loc" class="row" :class="[{ live: i === firstMissing }, rowState(node.loc, i)]" :style="{ '--rc': cloud ? 'var(--si-trip-m)' : 'var(--si-trip-l)' }">
        <p v-if="i === firstMissing && sweep">{{ liveParts[0] }}<span class="scan">{{ liveParts[1] }}</span>{{ liveParts[2] }}</p>
        <p v-else :class="{ still: i === firstMissing }">{{ node.text ?? '原文已按保留期限删除' }}</p>
        <p v-if="message.translation?.[node.loc]" :class="{ arrive: pending }">{{ message.translation[node.loc] }}</p>
        <p v-else-if="i === firstMissing" class="wait"><i class="cursor" aria-hidden="true" /><span class="num">正在译 · {{ elapsed }} 秒</span></p>
        <p v-else-if="pending" class="todo">待译</p>
        <p v-else class="miss">未完成翻译</p>
      </div>
    </div>
    <pre v-else-if="!loading" class="plain">{{ plain }}</pre>
  </div>
</template>
<style scoped>
.tv { display: flex; flex-direction: column; gap: 10px; }
.prog { margin: 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.paper { border: 1px solid var(--si-hair); border-radius: var(--si-radius-paper); background: var(--si-paper); box-shadow: var(--si-paper-shadow); overflow: hidden; }
.cols, .row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.cols span { padding: 8px 20px; border-bottom: 1px solid var(--si-hair-faint); background: var(--si-surface-2); color: var(--si-ink-3); font: var(--si-font-label-sm); }
.cols span + span { border-left: 1px solid var(--si-hair-faint); }
.row { position: relative; border-bottom: 1px solid var(--si-hair-faint); transition: background-color var(--si-d2) var(--si-ease-out); }
.row:last-child { border-bottom: 0; }
.row::before { content: ''; position: absolute; left: 7px; top: 8px; bottom: 8px; width: 4px; border-radius: 2px; background: var(--si-hair); }
.row.tk-done::before { background: var(--rc); }
.row.tk-live::before { background: color-mix(in srgb, var(--rc) 45%, transparent); }
.row:hover { background: var(--si-blue-tint); }
.row p { margin: 0; padding: 12px 20px; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--si-paper-ink-2); font: var(--si-font-reading); }
.row p + p { border-left: 1px solid var(--si-hair-faint); }
.miss { color: var(--si-ink-4) !important; }
.scan { color: var(--rc); background: color-mix(in srgb, var(--rc) 14%, transparent); border-radius: 2px; }
.still { background: color-mix(in srgb, var(--rc) 7%, transparent); }
.wait { display: flex; align-items: center; gap: 8px; }
.wait span { color: var(--si-ink-3); font: var(--si-font-caption); }
.todo { color: var(--si-ink-4) !important; font: var(--si-font-caption) !important; }
.arrive { animation: tv-in var(--si-d2) var(--si-ease-out); }
@keyframes tv-in { from { opacity: 0; transform: translateY(3px); } }
.cursor { display: inline-block; flex: none; width: 2px; height: 1.1em; background: var(--rc); animation: tv-blink 1s steps(2) infinite; }
@keyframes tv-blink { 50% { opacity: 0; } }
html:is([data-fx="lite"], [data-fx="off"]) :is(.arrive, .cursor) { animation: none; }
.plain { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
@media (max-width: 900px) { .cols, .row { grid-template-columns: 1fr; } .row p + p { border-left: 0; } .cols span:last-child { display: none; } }
</style>
