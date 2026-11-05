<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Lock, ShieldCheck } from '@lucide/vue'
import type { RunDetail } from '@/api/runs'
import type { TaskDetail } from '@/api/tasks'
import SentDrawer from '@/components/result/SentDrawer.vue'
import SiCount from '@/components/ui/SiCount.vue'
import { NOT_SENT } from '@/utils/labels'
import { useFx, useLoop, watchFrames } from '@/utils/motion'

/**
 * 审核中仪式（V31-U3 第五节）：舞台把“只有代号出网、回来在本机还原”演出来，四步状态只按真实数据显示，不演假进度。
 * 小胶囊不带任何文字或代号；完成后由父级立即跳转，不等动画。
 */
const props = defineProps<{ task: TaskDetail; run: RunDetail | null }>()
const fx = useFx()

const status = computed(() => props.run?.status ?? props.task.latest_run?.status ?? 'queued')
const receipt = computed(() => props.run?.receipt ?? null)
const queued = computed(() => status.value === 'queued')
const modelName = computed(() => props.run?.model_label ?? props.run?.model_name ?? receipt.value?.model ?? '模型')
const reviewDone = computed(() => ['completed', 'partial', 'failed'].includes(status.value))
const restoreDone = computed(() => ['completed', 'partial'].includes(status.value))
// 复检通过、发出第一段即打勾（进度 sent，V31-U5F-01）；旧轮次没有回执时，进入运行即视为已通过
const sentCount = computed(() => props.run?.progress?.sent ?? 0)
const recheckDone = computed(() => sentCount.value >= 1 || (receipt.value ? receipt.value.rechecks >= 1 : !queued.value))
const reviewText = computed(() => {
  if (queued.value) {
    const pos = props.run?.queue_position ?? props.task.queue_position
    return pos != null ? `排队中，前面还有 ${Math.max(0, pos - 1)} 个` : '排队中'
  }
  const p = props.run?.progress
  if (p && p.total > 1) return p.sent ? `已发出 ${p.sent}/${p.total} 段 · 已收到 ${p.done} 段` : `准备 ${p.total} 段`
  return sentCount.value ? `已发送，等待 ${modelName.value} 回复` : `${modelName.value} 审核中`
})
const steps = computed(() => {
  const list = [
    { label: '本地识别', done: true, text: receipt.value ? `已在本机保护 ${receipt.value.protected_total} 处` : '' },
    { label: '出口复检', done: recheckDone.value, text: '发送前检查中' },
    { label: '模型审核', done: reviewDone.value, text: reviewText.value },
    { label: '本地还原', done: restoreDone.value, text: '在本机还原代号' },
  ]
  const current = list.findIndex(s => !s.done)
  return list.map((s, i) => ({ ...s, state: s.done ? 'done' : i === current ? 'active' : 'wait', text: s.done ? (i === 0 ? s.text : '') : i === current ? s.text : '' }))
})

// 已用时间：排队从 queued_at，运行从 started_at；不显示预计剩余时间
const now = ref(Date.now())
const clock = setInterval(() => { now.value = Date.now() }, 1000)
const elapsed = computed(() => {
  const start = queued.value ? props.run?.queued_at : props.run?.started_at ?? props.run?.queued_at
  if (!start) return ''
  const s = Math.max(0, Math.floor((now.value - Date.parse(start)) / 1000))
  return Number.isFinite(s) ? `已用 ${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : ''
})
const notSent = computed(() => {
  const parts = ['原件', ...props.task.files.map(f => f.original_name)]
  for (const [key, n] of Object.entries(receipt.value?.not_sent ?? {})) if (n > 0) parts.push(`${NOT_SENT[key] ?? key} ${n}`)
  return parts.join(' · ')
})
const sentOpen = ref(false)

// 舞台：循环只在模型审核进行中、完整特效且页面可见时播放
const looping = useLoop(computed(() => !queued.value && !reviewDone.value))
const THUMB = [92, 70, 84, 56, 90, 64, 78, 48]
let stopFrames: (() => void) | undefined
onMounted(() => { if (fx.value === 'full') stopFrames = watchFrames() })
onBeforeUnmount(() => { clearInterval(clock); stopFrames?.() })
</script>

<template>
  <div class="ceremony">
    <div v-if="fx !== 'off'" class="stage" :class="`fx-${fx}`" aria-hidden="true">
      <div class="thumb">
        <span v-for="(w, i) in THUMB" :key="i" class="bar" :class="{ cap: i % 3 === 1 }" :style="{ width: `${w}%` }" />
        <span v-if="fx === 'full'" class="scan" />
      </div>
      <div v-if="fx === 'full'" class="fly">
        <span v-for="i in 10" :key="i" class="lane" :style="{ animationDelay: `${(i - 1) * 70}ms`, top: `${30 + (i % 5) * 14}px` }"><i class="pill" /></span>
      </div>
      <div class="vault">
        <Lock :size="28" :stroke-width="1.5" />
        <span class="vault-n"><SiCount :value="receipt?.protected_total ?? 0" /></span>
        <span class="caption">本机保险箱</span>
      </div>
      <div class="gate" :class="{ lit: recheckDone }">
        <ShieldCheck :size="26" :stroke-width="1.5" />
        <span class="caption">复检 {{ Math.max(receipt?.rechecks ?? 0, sentCount) }} 次 · 拦截 {{ receipt?.blocked ?? 0 }}</span>
      </div>
      <div class="track" :class="{ moving: looping }">
        <span class="track-label">只含代号 · 加密传输</span>
        <span v-for="i in 6" :key="i" class="lane" :style="{ animationDelay: `${-(i - 1) * 400}ms` }"><i class="pill" /></span>
      </div>
      <div class="model" :class="{ waiting: queued, breathing: looping }">
        <span class="halo" /><span class="dot" />
        <span class="caption model-name">{{ modelName }}</span>
        <span class="caption">{{ queued ? '排队中' : reviewDone ? '已返回' : reviewText }}</span>
      </div>
    </div>

    <ol class="steps" aria-live="polite">
      <li v-for="(s, i) in steps" :key="s.label" class="step" :class="s.state">
        <span class="mark">{{ s.state === 'done' ? '✓' : i + 1 }}</span>
        <span class="step-label">{{ s.label }}</span>
        <span v-if="s.text" class="step-text">{{ s.text }}</span>
      </li>
    </ol>
    <div class="foot">
      <span class="num">{{ elapsed }}<template v-if="elapsed"> · </template>可以离开页面，完成后自动打开结果</span>
      <el-button @click="$router.push('/history')">返回审核历史</el-button>
    </div>
    <div class="foot hint">
      <span class="not-sent">不发送：{{ notSent }}</span>
      <el-button v-if="run && (receipt?.calls ?? 0) >= 1" link type="primary" @click="sentOpen = true">查看本轮实际发送的文字</el-button>
    </div>
    <SentDrawer v-if="run" v-model="sentOpen" :run-id="run.id" />
  </div>
</template>

<style scoped>
.ceremony { display: flex; flex-direction: column; gap: 16px; }
.stage { position: relative; height: 260px; margin: -20px -24px 0; border-bottom: 1px solid var(--si-hair); overflow: hidden; color: var(--si-ink-2); }
.caption { font: var(--si-font-caption); color: var(--si-ink-3); white-space: nowrap; }
/* 文件缩略 */
.thumb { position: absolute; left: 5%; top: 60px; width: 96px; height: 128px; padding: 14px 12px; box-sizing: border-box; display: flex; flex-direction: column; gap: 8px; overflow: hidden; border-radius: var(--si-radius-paper); background: var(--si-paper); box-shadow: var(--si-paper-shadow); }
.bar { height: 5px; border-radius: 3px; background: var(--si-hair-strong); }
.bar.cap { background: var(--el-color-primary); width: 36% !important; }
.scan { position: absolute; left: 0; right: 0; top: 0; height: 2px; background: var(--el-color-primary); box-shadow: var(--si-glow-blue); animation: scan 1.3s var(--si-ease) both; }
@keyframes scan { from { transform: translateY(0); opacity: 1; } to { transform: translateY(126px); opacity: 0; } }
/* 飞入保险箱的小胶囊：lane 与区间等宽，用 translateX(100%) 移动整段距离 */
.fly { position: absolute; left: calc(5% + 96px); width: calc(22% - 96px); top: 60px; height: 128px; }
.lane { position: absolute; left: 0; width: 100%; height: 8px; }
.fly .lane { opacity: 0; animation: fly 750ms var(--si-ease) both; }
@keyframes fly { 0% { transform: translateX(0); opacity: 0; } 15% { opacity: 1; } 90% { opacity: 1; } 100% { transform: translateX(100%); opacity: 0; } }
.pill { display: block; width: 18px; height: 8px; border-radius: 4px; background: var(--el-color-primary); }
/* 保险箱、闸门、模型 */
.vault, .gate, .model { position: absolute; top: 72px; display: flex; flex-direction: column; align-items: center; gap: 6px; transform: translateX(-50%); }
.vault { left: 30%; color: var(--el-color-primary); }
.vault-n { font: var(--si-font-stat); color: var(--el-text-color-primary); }
.gate { left: 50%; padding: 12px 14px; border: 1px solid var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-surface-1); color: var(--si-ink-3); transition: border-color var(--si-dur-2) var(--si-ease), box-shadow var(--si-dur-2) var(--si-ease); }
.gate.lit { border-color: var(--el-color-primary); color: var(--el-color-primary); box-shadow: var(--si-glow-blue); }
.track { position: absolute; left: calc(50% + 70px); right: calc(14% + 40px); top: 104px; height: 8px; border-top: 2px dashed var(--si-hair-strong); overflow: visible; }
.track-label { position: absolute; left: 50%; top: 12px; transform: translateX(-50%); font: var(--si-font-caption); color: var(--si-ink-3); white-space: nowrap; }
.track .lane { top: -5px; opacity: 0; }
.track.moving .lane { opacity: 1; animation: run 2.4s linear infinite; }
@keyframes run { from { transform: translateX(-8%); } to { transform: translateX(100%); } }
.track .pill { width: 14px; }
.model { left: 86%; }
.dot, .halo { width: 44px; height: 44px; border-radius: 50%; }
.dot { position: relative; background: var(--si-ai); }
.halo { position: absolute; top: 0; background: var(--si-ai); opacity: 0; }
.model.waiting .dot { background: var(--si-ink-3); }
.model.breathing .halo { animation: breathe 2s var(--si-ease) infinite; }
@keyframes breathe { 0%, 100% { opacity: .15; transform: scale(1); } 50% { opacity: .35; transform: scale(1.35); } }
.model-name { max-width: 160px; overflow: hidden; text-overflow: ellipsis; font: var(--si-font-label-sm); color: var(--el-text-color-primary); }
/* 四步与说明：三个特效级别完全相同 */
.steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0; padding: 0; list-style: none; }
.step { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; color: var(--si-ink-3); }
.mark { flex: none; width: 22px; height: 22px; display: grid; place-items: center; border: 1px solid var(--si-hair-strong); border-radius: 50%; font: var(--si-font-label-sm); }
.step-label { font: var(--si-font-label); }
.step-text { flex-basis: 100%; padding-left: 30px; font: var(--si-font-caption); }
.step.done { color: var(--el-text-color-primary); }
.step.done .mark { border-color: var(--el-color-primary); background: var(--el-color-primary); color: var(--si-white); }
.step.active { color: var(--el-text-color-primary); }
.step.active .mark { border-color: var(--el-color-primary); color: var(--el-color-primary); }
.step.active .step-text { color: var(--el-color-primary); }
.foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; font: var(--si-font-meta); }
.not-sent { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { .scan, .fly, .track .lane, .model .halo { display: none; } }
</style>
