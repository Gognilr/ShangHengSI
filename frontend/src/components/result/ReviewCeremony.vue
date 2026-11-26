<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Calculator, Check, Sparkle, Sparkles } from '@lucide/vue'
import type { Finding, RunDetail } from '@/api/runs'
import { assistantApi } from '@/api/assistant'
import ResultCanvas from '@/components/result/ResultCanvas.vue'
import MarkedText from '@/components/result/MarkedText.vue'
import type { CanvasAnnotation } from '@/components/ConfirmCanvas.vue'
import type { TaskDetail } from '@/api/tasks'
import SentDrawer from '@/components/result/SentDrawer.vue'
import { NOT_SENT, severityLabel } from '@/utils/labels'
import { useFx } from '@/utils/motion'

/**
 * 审核中仪式（V31-U3 第五节）：舞台把“只有代号出网、回来在本机还原”演出来，四步状态只按真实数据显示，不演假进度。
 * 小胶囊不带任何文字或代号；完成后由父级立即跳转，不等动画。
 */
const props = defineProps<{ task: TaskDetail; run: RunDetail | null }>()
const fx = useFx()
// 会话复用 task 外观，但节点必须通过会话接口读取。
const isSession = computed(() => 'review_running' in props.task)
const nodeLoader = (id: string, fid: string) => assistantApi.nodes(id, fid)
const rawLoader = (id: string, fid: string) => assistantApi.raw(id, fid)
const selected = ref('')
const canvas = ref<InstanceType<typeof ResultCanvas>>()
const findings = computed(() => props.run?.findings ?? [])
const annotations = computed<CanvasAnnotation[]>(() => findings.value.filter(f => f.locs.length).map((f, i) =>
  ({ id: f.id, no: String(i + 1).padStart(2, '0'), severity: f.severity, loc: f.locs[0]!, excerpt: f.quote_excerpt })))
function selectFinding(f: Finding) {
  selected.value = f.id
  if (f.locs[0]) void canvas.value?.locate(f.locs[0], f.id)
}
function selectCanvas(id: string) { selected.value = id }

const status = computed(() => props.run?.status ?? props.task.latest_run?.status ?? 'queued')
const receipt = computed(() => props.run?.receipt ?? null)
const queued = computed(() => status.value === 'queued')
const modelName = computed(() => props.run?.model_label ?? props.run?.model_name ?? receipt.value?.model ?? '模型')
const reviewDone = computed(() => ['completed', 'partial', 'failed'].includes(status.value))
const sentCount = computed(() => props.run?.progress?.sent ?? 0)
const reviewText = computed(() => {
  if (queued.value) {
    const pos = props.run?.queue_position ?? props.task.queue_position
    return pos != null ? `排队中，前面还有 ${Math.max(0, pos - 1)} 个` : '排队中'
  }
  const p = props.run?.progress
  // 流式接收的字数（V31-U5F-03）：让人看到模型在出字，而不是卡住
  const got = p?.received ? `已收到约 ${p.received.toLocaleString('zh-CN')} 字` : ''
  if (p && p.total > 1) {
    if (!p.sent) return `准备 ${p.total} 段`
    const head = p.sent < p.total ? `已发出 ${p.sent}/${p.total} 段` : `已回复 ${p.done}/${p.total} 段`
    return got ? `${head} · ${got}` : `${head} · 等待回复`
  }
  if (got) return got
  return sentCount.value ? `已发送，等待 ${modelName.value} 回复` : `${modelName.value} 审核中`
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

onBeforeUnmount(() => { clearInterval(clock) })
const doneCount = computed(() => findings.value.length)
</script>

<template>
  <div class="ceremony">
    <div class="live-canvas">
      <ResultCanvas ref="canvas" :task="task" :note="run?.user_note ?? null" :annotations="annotations" :local-locs="[]"
        :selected-finding-id="selected" :raw-highlight="[]" :ask-enabled="false"
        :node-loader="isSession ? nodeLoader : undefined" :raw-loader="isSession ? rawLoader : undefined" @select="selectCanvas" />
      <span v-if="!reviewDone && fx !== 'off'" class="scanline" aria-hidden="true" />
    </div>
    <aside class="live-rail" aria-live="polite">
      <div class="rail-head">
        <span class="tab">意见</span>
        <span class="ai-tag"><Sparkle :size="13" :stroke-width="2" />{{ modelName }}</span>
      </div>
      <div class="rail-scroll">
        <div class="calc-fold"><span class="calc-ic"><Calculator :size="14" :stroke-width="1.75" /></span><b>本机计算 · 结果确定</b><span class="ok-badge"><Check :size="12" :stroke-width="2.5" />本机已算完</span></div>
        <div class="ai-wait" :class="{ queued }">
          <div class="ai-orb"><Sparkles :size="22" :stroke-width="1.75" /></div>
          <b>{{ queued ? '排队等待模型' : reviewDone ? '模型已返回，正在本机还原' : '模型正在逐条审阅遮住后的文字' }}</b>
          <span class="hint">{{ doneCount ? `已收到 ${doneCount} 条意见，结果会一条条出现` : reviewText }}</span>
        </div>
        <TransitionGroup name="ic" tag="div" class="cards">
          <article v-for="(f, i) in findings" :key="f.id" class="ic-card" :class="[`sev-${f.severity}`, { on: selected === f.id }]" role="button" tabindex="0" @click="selectFinding(f)" @keydown.enter="selectFinding(f)">
            <div class="ic-top"><span class="sev-badge"><i />{{ severityLabel(f.severity) }}</span><span v-if="f.location" class="loc-badge">{{ f.location }}</span><span class="ic-no num">{{ String(i + 1).padStart(2, '0') }}</span></div>
            <b class="ic-title"><MarkedText :text="f.title" :marks="f.marks.title" /></b>
            <p class="ic-p"><MarkedText :text="f.problem" :marks="f.marks.problem" /></p>
          </article>
        </TransitionGroup>
        <div v-if="!reviewDone" class="sk-card" aria-hidden="true"><span class="sk" style="width: 30%" /><span class="sk" style="width: 85%; height: 16px" /><span class="sk" style="width: 65%" /></div>
      </div>
      <div class="rail-foot">
        <span class="not-sent" :title="notSent">不发送：{{ notSent }}</span>
        <div class="foot-row">
          <el-button v-if="run && (receipt?.calls ?? 0) >= 1" link type="primary" size="small" @click="sentOpen = true">本轮实际发送的文字</el-button>
          <span class="hint num">{{ elapsed }}</span>
        </div>
      </div>
    </aside>
    <SentDrawer v-if="run" v-model="sentOpen" :run-id="run.id" />
  </div>
</template>

<style scoped>
/* 审核中（澄明 8.4 ③）：纸面扫描线 + 右栏紫色等待；进度只在顶部工具条 */
.ceremony { display: flex; gap: 16px; min-height: 0; height: 100%; }
.live-canvas { position: relative; display: flex; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.scanline { position: absolute; left: 8%; right: 8%; top: 56px; height: 2px; border-radius: 2px; background: linear-gradient(90deg, transparent, var(--si-brand-2), var(--si-ai), transparent); box-shadow: 0 0 12px color-mix(in srgb, var(--si-ai) 50%, transparent); pointer-events: none; animation: cer-scan 3.2s var(--si-ease-io) infinite; }
@keyframes cer-scan { 0% { transform: translateY(0); opacity: 0; } 10% { opacity: 1; } 90% { opacity: 1; } 100% { transform: translateY(60vh); opacity: 0; } }
:root[data-fx="lite"] .scanline { animation-duration: 6s; }
.live-rail { flex: 0 0 440px; min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); overflow: hidden; }
.rail-head { flex: none; display: flex; align-items: center; gap: 12px; height: 48px; padding: 0 16px; border-bottom: 1px solid var(--si-hair-faint); }
.tab { position: relative; display: inline-flex; align-items: center; height: 48px; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.tab::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; border-radius: 2px; background: var(--si-brand-2); }
.ai-tag { display: inline-flex; align-items: center; gap: 6px; height: 24px; margin-left: auto; padding: 0 10px; border-radius: var(--si-radius-badge); background: var(--si-ai-soft); color: var(--si-ai-ink); font: var(--si-font-badge); }
.ai-tag svg { color: var(--si-ai); }
.rail-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }
.calc-fold { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--si-radius-md); background: var(--si-card-2); border: 1px solid var(--si-hair-faint); font: var(--si-font-label); }
.calc-ic { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 7px; background: var(--si-blue-tint); color: var(--si-brand-2); }
.ok-badge { display: inline-flex; align-items: center; gap: 4px; height: 22px; margin-left: auto; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-ok-soft); color: var(--si-ok-ink); font: var(--si-font-badge); }
.ai-wait { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 22px 12px 18px; border-radius: var(--si-radius-card); background: var(--si-ai-soft); text-align: center; }
.ai-wait b { color: var(--si-ai-ink); font: var(--si-font-label); font-weight: 600; }
.ai-orb { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 4px; border-radius: 50%; background: var(--si-surface-1); color: var(--si-ai); box-shadow: 0 0 0 6px color-mix(in srgb, var(--si-ai) 12%, transparent); animation: cer-breathe 2.4s var(--si-ease-io) infinite; }
.ai-wait.queued .ai-orb { color: var(--si-ink-3); animation: none; }
@keyframes cer-breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.06); } }
:root[data-fx="lite"] .ai-orb, :root[data-fx="off"] .ai-orb { animation: none; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.ic-card { padding: 12px 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-md); background: var(--si-surface-1); cursor: pointer; transition: box-shadow var(--si-d2) var(--si-ease-out), border-color var(--si-d2) var(--si-ease-out); }
.ic-card:hover { border-color: var(--si-hair-strong); }
.ic-card.on { border-color: var(--si-brand-2); }
.ic-top { display: flex; align-items: center; gap: 6px; }
.sev-badge, .loc-badge { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: var(--si-radius-badge); font: var(--si-font-badge); }
.sev-badge i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.sev-high .sev-badge { background: var(--si-danger-soft); color: var(--si-danger-ink); }
.sev-medium .sev-badge { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.sev-low .sev-badge { background: var(--si-info-soft); color: var(--si-info-ink); }
.loc-badge { max-width: 180px; overflow: hidden; border: 1px solid var(--si-hair); color: var(--si-ink-3); text-overflow: ellipsis; white-space: nowrap; }
.ic-no { margin-left: auto; color: var(--si-ink-4); font: var(--si-font-code-sm); }
.ic-title { display: block; margin-top: 8px; color: var(--si-ink-1); font: var(--si-font-h4); }
.ic-p { margin: 4px 0 0; color: var(--si-ink-2); font: var(--si-font-body); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ic-enter-active { transition: opacity var(--si-d3) var(--si-ease-out), transform var(--si-d3) var(--si-ease-spring); }
.ic-enter-from { opacity: 0; transform: translateY(14px) scale(.98); }
.sk-card { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px dashed var(--si-hair); border-radius: var(--si-radius-md); }
.sk { display: block; height: 12px; border-radius: var(--si-radius-tag); background: linear-gradient(90deg, var(--si-surface-2), color-mix(in srgb, var(--si-surface-2) 40%, var(--si-surface-1)), var(--si-surface-2)); background-size: 200% 100%; animation: cer-shine 1.6s linear infinite; }
@keyframes cer-shine { from { background-position: 200% 0; } to { background-position: -200% 0; } }
:root[data-fx="off"] .sk { animation: none; }
.rail-foot { flex: none; display: flex; flex-direction: column; gap: 2px; padding: 10px 16px; border-top: 1px solid var(--si-hair-faint); color: var(--si-ink-3); font: var(--si-font-caption); }
.foot-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.not-sent { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hint { margin: 0; }
</style>
