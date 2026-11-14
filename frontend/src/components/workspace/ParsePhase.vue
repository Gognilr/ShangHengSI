<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Check, Loader, X } from '@lucide/vue'
import type { Session, SessionFile } from '@/api/assistant'
import { useStale } from '@/utils/stale'

/**
 * 解析阶段（V321-E-06）：每个文件一行显示当前阶段，最后一行是整批的“识别敏感信息与本地校验”。
 * 只有 OCR 知道总页数，才画确定进度条；其他阶段只显示文字，不画假进度。
 */
const props = defineProps<{ session: Session }>()
const emit = defineEmits<{ remove: [file: SessionFile] }>()

const now = ref(Date.now())
const timer = setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(timer))
const started = computed(() => { const t = new Date(props.session.last_activity_at).getTime(); return Number.isFinite(t) ? t : now.value })
const elapsed = computed(() => Math.max(0, Math.floor((now.value - started.value) / 1000)))

const failed = computed(() => props.session.status === 'parse_failed')
const rows = computed(() => props.session.files.filter(f => !f.media && !f.confirmed_at).map(f => {
  const got = props.session.parse_progress?.files?.[f.id]
  const stage = f.parse_state === 'done' || got?.stage === 'done' ? 'done' : got?.stage ?? 'waiting'
  return { file: f, stage, done: got?.done ?? null, total: got?.total ?? null }
}))
const allDone = computed(() => rows.value.length > 0 && rows.value.every(r => r.stage === 'done'))
const detecting = computed(() => props.session.parse_progress?.phase === 'detect' || allDone.value)

const STAGE_TEXT: Record<string, string> = { waiting: '等待', converting: '转换旧格式…', reading: '读取正文…', done: '完成' }
function text(row: { stage: string; done: number | null; total: number | null }) {
  if (row.stage === 'ocr') return row.total ? `OCR 第 ${Math.min((row.done ?? 0) + 1, row.total)} / ${row.total} 页` : 'OCR 识别中…'
  return STAGE_TEXT[row.stage] ?? '处理中…'
}
const percent = (row: { done: number | null; total: number | null }) => row.total ? Math.round(((row.done ?? 0) / row.total) * 100) : 0
// 同一阶段停留超过约 10 秒没有变化：补一句“耗时较长”（V321-E-12）；整页一条，按各行当前阶段取第一条在跑的
const stageKey = computed(() => rows.value.map(r => `${r.stage}:${r.done}`).join(',') + (detecting.value ? ':d' : ''))
const slow = useStale(stageKey, 10000)
const slowText = computed(() => {
  if (!slow.value || failed.value) return ''
  if (rows.value.some(r => r.stage === 'ocr')) return '扫描件逐页识别，耗时较长'
  if (rows.value.some(r => r.stage === 'converting')) return '旧格式需要先转换，耗时较长'
  return detecting.value ? '内容较多，识别需要一些时间' : ''
})
const badge = (f: SessionFile) => f.ext.toUpperCase().slice(0, 4)
</script>

<template>
  <section class="parse" aria-label="文件解析进度">
    <div class="parse-card">
      <h2 v-if="!failed" role="status">正在本机解析<span class="num"> · 已用 {{ elapsed }} 秒</span></h2>
      <h2 v-else>解析未完成</h2>
      <el-alert v-if="failed && session.error_message" type="error" show-icon :closable="false" :title="session.error_message" />
      <ul class="rows">
        <li v-for="r in rows" :key="r.file.id" class="row" :class="`row--${r.stage}`">
          <span class="badge">{{ badge(r.file) }}</span>
          <span class="name" :title="r.file.original_name">{{ r.file.original_name }}</span>
          <span class="state">
            <Check v-if="r.stage === 'done'" :size="14" :stroke-width="2" />
            <Loader v-else-if="!failed && r.stage !== 'waiting'" :size="14" :stroke-width="1.75" class="spin" />
            <span>{{ text(r) }}</span>
          </span>
          <el-progress v-if="r.stage === 'ocr' && r.total" class="bar" :percentage="percent(r)" :show-text="false" :stroke-width="4" />
          <el-button v-if="failed" link type="danger" size="small" @click="emit('remove', r.file)"><X :size="14" :stroke-width="1.75" />移除</el-button>
        </li>
        <li v-if="!failed" class="row" :class="{ 'row--done': false }">
          <span class="badge badge--sys">本机</span>
          <span class="name">识别敏感信息与本地校验</span>
          <span class="state">
            <Loader v-if="detecting" :size="14" :stroke-width="1.75" class="spin" />
            <span>{{ detecting ? '识别中…' : '等待' }}</span>
          </span>
        </li>
      </ul>
      <p v-if="slowText" class="hint slow" role="status">{{ slowText }}</p>
      <p class="hint">文件不离开本机，可以离开页面，稍后回来继续。</p>
    </div>
  </section>
</template>

<style scoped>
.parse { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 20px; display: flex; justify-content: center; align-items: flex-start; }
.parse-card { width: min(640px, 100%); display: flex; flex-direction: column; gap: 14px; padding: 18px 20px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.num { color: var(--si-ink-3); font-variant-numeric: tabular-nums; }
.rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 4px 10px; padding: 8px 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: var(--el-bg-color); }
.badge { display: grid; place-items: center; min-width: 36px; height: 24px; padding: 0 6px; border-radius: 6px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); }
.badge--sys { background: var(--si-surface-2); color: var(--si-ink-2); }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); }
.state { display: inline-flex; align-items: center; gap: 6px; color: var(--si-ink-2); font: var(--si-font-caption); }
.row--done .state { color: var(--el-color-success); }
.bar { grid-column: 2 / 4; }
.spin { animation: parse-spin 1.2s linear infinite; }
@keyframes parse-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
