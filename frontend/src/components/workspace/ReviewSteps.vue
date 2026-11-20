<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Check, EyeOff, LoaderCircle, TriangleAlert } from '@lucide/vue'

/**
 * 文件审核的进度（澄明 8.4、第七节“进度只出现在一处”）：放在工作区顶部工具条上。
 * 本机读取 → 确认遮住的内容 → 模型审核 → 还原；审核完成后收成一个徽标“已完成 · 用时 x”。
 */
export interface ReviewProgress {
  stage: 'parsing' | 'confirm' | 'setup' | 'running' | 'result' | 'local' | 'loading'
  /** 审核中：开始时间（ISO），用于计时 */
  since?: string | null
  /** 已完成：用时（秒） */
  seconds?: number | null
  /** 发送前复检拦下的处数（确认页） */
  blocked?: number
  /** 审核中：排队位置 */
  queue?: number | null
  /** 结果的轮次状态（failed / partial 等） */
  status?: string
}
const props = defineProps<{ progress: ReviewProgress }>()

const now = ref(Date.now())
let timer: number | undefined
watch(() => props.progress.stage, stage => {
  window.clearInterval(timer)
  if (stage === 'running') timer = window.setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
onBeforeUnmount(() => window.clearInterval(timer))

const clock = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
const elapsed = computed(() => {
  const since = props.progress.since ? Date.parse(props.progress.since) : NaN
  return Number.isFinite(since) ? clock(Math.max(0, Math.floor((now.value - since) / 1000))) : ''
})
const took = computed(() => {
  const s = props.progress.seconds
  if (s == null || !Number.isFinite(s)) return ''
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${Math.max(1, Math.round(s))} 秒`
})
type State = 'done' | 'cur' | 'ai' | 'warn' | 'wait'
const steps = computed<{ label: string; state: State }[]>(() => {
  const p = props.progress
  const at = { parsing: 0, confirm: 1, setup: 1, running: 2, result: 4, local: 4, loading: -1 }[p.stage]
  const label1 = p.blocked ? `发送前复检拦下 ${p.blocked} 处` : at > 1 ? '确认遮住' : '确认遮住的内容'
  const running = p.queue != null && p.queue > 0 ? `排队中 · 前面 ${Math.max(0, p.queue - 1)} 个` : `模型审核${elapsed.value ? ` · ${elapsed.value}` : ''}`
  const labels = ['本机读取', label1, at === 2 ? running : '模型审核', '还原']
  return labels.map((label, i) => ({
    label,
    state: i < at ? 'done' : i === at ? (i === 1 && p.blocked ? 'warn' : i === 2 ? 'ai' : 'cur') : 'wait',
  }))
})
const finished = computed(() => props.progress.stage === 'result')
</script>

<template>
  <div v-if="progress.stage !== 'loading' && progress.stage !== 'local'" class="rsteps" role="status" aria-label="审核进度">
    <span v-if="finished" class="done-badge" :class="{ warn: progress.status === 'failed' || progress.status === 'partial' }">
      <component :is="progress.status === 'failed' ? TriangleAlert : Check" :size="14" :stroke-width="2" />
      {{ progress.status === 'failed' ? '审核未完成' : progress.status === 'partial' ? '部分完成' : '已完成' }}<template v-if="took && progress.status !== 'failed'"> · 用时 {{ took }}</template>
    </span>
    <template v-else>
      <template v-for="(s, i) in steps" :key="i">
        <i v-if="i" class="sep" :class="{ on: s.state !== 'wait' }" />
        <span class="step" :class="`is-${s.state}`">
          <span class="ck">
            <Check v-if="s.state === 'done'" :size="12" :stroke-width="2.5" />
            <LoaderCircle v-else-if="s.state === 'ai' || (s.state === 'cur' && i === 0)" class="spin" :size="13" :stroke-width="2" />
            <TriangleAlert v-else-if="s.state === 'warn'" :size="12" :stroke-width="2" />
            <EyeOff v-else-if="s.state === 'cur'" :size="11" :stroke-width="2" />
          </span>
          <span class="num">{{ s.label }}</span>
        </span>
      </template>
      <span v-if="progress.stage === 'running'" class="leave">可以离开页面</span>
    </template>
  </div>
</template>

<style scoped>
.rsteps { display: flex; align-items: center; gap: 6px; min-width: 0; font: var(--si-font-label-sm); }
.step { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px 0 4px; border-radius: 999px; color: var(--si-ink-4); white-space: nowrap; transition: background-color var(--si-d2) var(--si-ease-out), color var(--si-d2) var(--si-ease-out); }
.ck { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; border: 1.5px solid var(--si-hair-strong); }
.is-done { color: var(--si-ink-2); }
.is-done .ck { border-color: var(--si-brand); background: var(--si-brand); color: var(--si-white); }
.is-cur { background: var(--si-blue-tint); color: var(--si-blue-ink); }
.is-cur .ck { border-color: var(--si-brand-2); background: var(--si-surface-1); color: var(--si-brand-2); }
.is-ai { background: var(--si-ai-soft); color: var(--si-ai-ink); }
.is-ai .ck { border: 0; color: var(--si-ai); }
.is-warn { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.is-warn .ck { border: 0; color: var(--si-warn); }
.sep { flex: none; width: 14px; height: 1.5px; border-radius: 1px; background: var(--si-hair-strong); }
.sep.on { background: var(--si-brand-2); }
.spin { animation: rs-spin 1s linear infinite; }
@keyframes rs-spin { to { transform: rotate(360deg); } }
:root[data-fx="off"] .spin, :root[data-fx="lite"] .spin { animation-duration: 2.4s; }
.leave { margin-left: 6px; color: var(--si-ink-4); font: var(--si-font-caption); white-space: nowrap; }
.done-badge { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px; border-radius: 999px; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-label-sm); white-space: nowrap; animation: rs-pop var(--si-d3) var(--si-ease-spring) both; }
.done-badge.warn { background: var(--si-warn-soft); color: var(--si-warn-ink); }
@keyframes rs-pop { from { opacity: 0; transform: scale(.9); } to { opacity: 1; transform: none; } }
@media (max-width: 1440px) { .leave { display: none; } }
</style>
