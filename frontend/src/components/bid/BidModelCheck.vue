<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronRight, LoaderCircle, Sparkles } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidGroupLabel, type BidModelView } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidSentDrawer from './BidSentDrawer.vue'

// 模型补查（澄明 8.5，可选）：问题清单底部一张紫色卡片。只查 ★ 与否决条款中本机未找到的；用户点了才准备片段，
// 片段在抽屉里按投标人核对（可切 AI 视野），确认后才发送；结果并入问题清单，带“模型补查”徽标。
const props = defineProps<{ projectId: string; stateKey: string; lookup: number }>()
const emit = defineEmits<{ changed: [] }>()
const data = ref<BidModelView>(), error = ref(''), busy = ref(false), open = ref(false)
const sentOpen = ref(false)
const tab = ref(''), view = ref<'original' | 'ai'>('ai'), showReqs = ref(false)
const viewOptions: { label: string; value: 'original' | 'ai' }[] = [{ label: '模型将看到的', value: 'ai' }, { label: '原文', value: 'original' }]
const tabs = computed(() => data.value?.bidders.map(b => ({ value: b.id, label: `${b.name}（${b.code.slice(1, -1)}）` })) ?? [])
const current = computed(() => data.value?.bidders.find(b => b.id === tab.value) ?? data.value?.bidders[0])
const remainingText = computed(() => {
  const r = data.value?.receipt?.remaining
  if (!r) return ''
  const parts = [r.monthly !== null ? `本月剩余 ${r.monthly} 次` : '', r.daily !== null ? `今日个人剩余 ${r.daily} 次` : ''].filter(Boolean)
  return parts.length ? parts.join('，') : '不限次数'
})
const notEnough = computed(() => {
  const r = data.value?.receipt
  return !!r && [r.remaining.monthly, r.remaining.daily].some(v => v !== null && v < r.calls)
})
const ocrPending = computed(() => !data.value?.state && !!data.value?.ocr && data.value.ocr.done < data.value.ocr.total)
const retry = computed(() => data.value?.state === 'failed' && !!data.value.receipt && data.value.receipt.calls < data.value.receipt.total_calls)
const awaiting = computed(() => (data.value?.state === 'awaiting' || data.value?.state === 'failed') && !!data.value.receipt)

let timer: ReturnType<typeof setTimeout> | undefined
let request = 0, mounted = true
function schedule() {
  clearTimeout(timer)
  if (mounted && (data.value?.state === 'preparing' || data.value?.state === 'running' || ocrPending.value))
    timer = setTimeout(() => void load(), ocrPending.value ? 10000 : 2000)
}
async function load() {
  const current = ++request
  clearTimeout(timer)
  try {
    const next = await bidApi.model(props.projectId)
    if (current !== request || !mounted) return
    error.value = ''
    // 状态或进度变化时通知项目页刷新（结果随批次写入，问题清单跟着更新）
    if (data.value && (data.value.state !== next.state || data.value.progress.done !== next.progress.done)) emit('changed')
    data.value = next
    if (!tab.value && next.bidders.length) tab.value = next.bidders[0]!.id
  } catch (e) { if (current === request && mounted) error.value = e instanceof ApiError ? e.message : '模型补查状态读取失败' }
  if (current === request) schedule()
}
watch(() => props.stateKey, () => { if (!busy.value) void load() }, { immediate: true })
onBeforeUnmount(() => { mounted = false; ++request; clearTimeout(timer) })

async function act(action: 'prepare' | 'confirm' | 'cancel') {
  ++request
  clearTimeout(timer)
  busy.value = true; error.value = ''
  try {
    const next = await bidApi.modelAction(props.projectId, action)
    if (!mounted) return
    data.value = next
    open.value = false
    emit('changed')
  } catch (e) { if (mounted) error.value = e instanceof ApiError ? e.message : '操作失败，请重试' }
  finally { busy.value = false; schedule() }
}
const hidden = computed(() => !!data.value && !data.value.state && !ocrPending.value && !props.lookup && !(data.value.sent_count ?? 0))
</script>

<template>
  <section v-if="data && !hidden" class="ai-card" aria-label="模型补查">
    <BidSentDrawer v-model="sentOpen" :project-id="projectId" />
    <span class="ic" aria-hidden="true"><LoaderCircle v-if="data.state === 'preparing' || data.state === 'running'" class="spin" :size="18" :stroke-width="1.75" /><Sparkles v-else :size="18" :stroke-width="1.75" /></span>
    <div class="grow">
      <template v-if="ocrPending">
        <b>扫描页还在识别（{{ data.ocr!.done }} / {{ data.ocr!.total }}）</b>
        <span>识别完后可把本机未找到的 ★ 与否决条款交模型补查</span>
      </template>
      <template v-else-if="!data.state">
        <b>★ 与否决条款中本机未找到的 {{ lookup }} 处，可交模型补查</b>
        <span>只发送遮住名称后的相关段落，投标人只以代号出现</span>
      </template>
      <template v-else-if="data.state === 'preparing'">
        <b>正在本机找相关段落并遮住名称</b>
        <span>可以先看本机检查的结果</span>
      </template>
      <template v-else-if="awaiting">
        <b><template v-if="data.state === 'failed'">模型补查没有完成，</template>将发送 {{ data.receipt!.snippets }} 段，约 {{ data.receipt!.chars }} 字</b>
        <span>遮住 {{ data.receipt!.protected }} 处 · 本次 {{ data.receipt!.calls }} 次调用（{{ remainingText }}）<template v-if="data.state === 'failed' && data.error"> · {{ data.error }}</template></span>
      </template>
      <template v-else-if="data.state === 'failed'">
        <b>模型补查没有完成</b><span>{{ data.error }}</span>
      </template>
      <template v-else-if="data.state === 'running'">
        <b>模型正在补查 · {{ data.progress.done }} / {{ data.progress.total }} 家</b>
        <span>结果随后并入问题清单，带“模型补查”徽标</span>
      </template>
      <template v-else-if="data.state === 'done'">
        <b>模型补查已完成 {{ data.progress.total }} 家</b>
        <span>结果已并入问题清单，带“模型补查”徽标，请核实</span>
      </template>
      <span v-if="notEnough && awaiting" class="err">剩余调用次数不足，请联系管理员。</span>
      <span v-if="error" class="err" role="alert">{{ error }}</span>
      <span class="links">
        <button v-if="awaiting" type="button" class="link" @click="open = true">查看将发送的内容</button>
        <button v-if="(data.sent_count ?? 0) > 0 || ['done', 'running', 'failed'].includes(data.state ?? '')" type="button" class="link" @click="sentOpen = true">查看实际发送内容</button>
      </span>
    </div>
    <div class="acts">
      <el-button v-if="!data.state && !ocrPending" size="small" class="ai-btn" :loading="busy" @click="act('prepare')">交模型补查</el-button>
      <template v-else-if="awaiting">
        <el-button v-if="data.state !== 'failed'" size="small" text :disabled="busy" @click="act('cancel')">不发送</el-button>
        <el-button size="small" class="ai-btn" :loading="busy" :disabled="notEnough" @click="act('confirm')">{{ retry ? '继续未完成的部分' : '确认发送' }}</el-button>
      </template>
      <el-button v-else-if="data.state === 'failed' || data.state === 'done'" size="small" text :loading="busy" @click="act('prepare')">重新准备</el-button>
    </div>

    <el-drawer v-model="open" size="min(720px, 92vw)" append-to-body title="将交模型补查的内容">
      <template v-if="data.receipt && current">
        <p class="d-lede">只发送遮住名称后的段落与条款；原件、图片、报价金额和文件名留在本机。投标人以代号出现，结果在本机还原。</p>
        <ul v-if="data.receipt.notes.length" class="d-notes"><li v-for="note in data.receipt.notes" :key="note">{{ note }}</li></ul>
        <div class="d-head">
          <SiSegmented v-model="tab" :options="tabs" size="small" ariaLabel="投标人" />
          <SiSegmented v-model="view" :options="viewOptions" size="small" ariaLabel="片段视图" />
        </div>
        <button type="button" class="d-toggle" :aria-expanded="showReqs" @click="showReqs = !showReqs">
          <ChevronRight :size="14" :stroke-width="1.75" :class="{ turned: showReqs }" aria-hidden="true" />一同发送的条款<span class="num">{{ data.requirements.length }}</span>
        </button>
        <ol v-if="showReqs" class="d-reqs">
          <li v-for="r in data.requirements" :key="r.id"><span class="code num">{{ r.n }}</span><span class="hint">{{ bidGroupLabel[r.group] }}</span><span>{{ view === 'ai' ? r.ai_text : r.text }}</span></li>
        </ol>
        <article v-for="s in current.snippets" :key="s.id" class="d-snip">
          <header><span class="code num">{{ s.id }}</span><span class="hint">{{ view === 'ai' ? current.code : current.name }}<template v-if="view === 'original'"> · {{ s.file }}<template v-if="s.page"> 第 {{ s.page }} 页</template></template></span><span class="hint for">用于 {{ s.requirements.join('、') }}</span></header>
          <p>{{ view === 'ai' ? s.ai_text : s.text }}</p>
        </article>
        <p v-if="!current.snippets.length" class="hint">没有找到这家的相关段落。</p>
      </template>
      <template #footer>
        <el-button @click="open = false">关闭</el-button>
        <el-button class="ai-btn" :loading="busy" :disabled="notEnough" @click="act('confirm')">{{ retry ? '继续未完成的部分' : '确认发送' }}</el-button>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.ai-card { display: flex; align-items: flex-start; gap: 12px; margin: 12px 4px 0; padding: 12px 14px; border-radius: 12px; background: var(--si-ai-soft); color: var(--si-ai-ink); }
.ic { flex: none; display: grid; place-items: center; width: 28px; height: 28px; margin-top: 1px; border-radius: 8px; background: color-mix(in srgb, var(--si-ai) 14%, transparent); }
.spin { animation: ai-spin 1s linear infinite; }
@keyframes ai-spin { to { transform: rotate(360deg); } }
:global(html[data-fx="off"]) .ai-card .spin { animation: none; }
.grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.grow b { font: var(--si-font-label); font-weight: 600; }
.grow > span { font: var(--si-font-caption); opacity: .88; }
.err { color: var(--si-danger-ink); opacity: 1 !important; }
.links { display: flex; gap: 12px; margin-top: 2px; }
.link { padding: 0; border: 0; background: none; font: var(--si-font-caption); color: var(--si-ai-ink); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.acts { flex: none; display: flex; align-items: center; gap: 4px; align-self: center; }
.ai-btn { --el-button-bg-color: var(--si-ai); --el-button-border-color: var(--si-ai); --el-button-text-color: var(--si-white); --el-button-hover-bg-color: var(--si-ai-2); --el-button-hover-border-color: var(--si-ai-2); --el-button-hover-text-color: var(--si-white); }
.d-lede { margin: 0 0 12px; font: var(--si-font-body); color: var(--si-ink-2); }
.d-notes { margin: 0 0 12px; padding-left: 18px; font: var(--si-font-meta); color: var(--si-warn-ink); }
.d-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.d-toggle { display: flex; align-items: center; gap: 8px; width: 100%; padding: 0 0 8px; border: 0; border-bottom: 1px solid var(--si-hair); background: transparent; font: var(--si-font-label); color: var(--si-ink-2); cursor: pointer; text-align: left; }
.d-toggle svg { transition: transform var(--si-d2) var(--si-ease-out); }
.d-toggle .turned { transform: rotate(90deg); }
.d-toggle .num { color: var(--si-ink-4); }
.d-reqs { margin: 0; padding: 0; list-style: none; }
.d-reqs li { display: grid; grid-template-columns: 40px 120px minmax(0, 1fr); gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-1); }
.code { font: var(--si-font-code-sm); color: var(--si-ink-3); }
.d-snip { padding: 10px 0 12px; border-bottom: 1px solid var(--si-hair-faint); }
.d-snip header { display: flex; align-items: baseline; gap: 10px; }
.d-snip .for { margin-left: auto; }
.d-snip p { margin: 6px 0 0; font: var(--si-font-body); color: var(--si-ink-1); white-space: pre-wrap; overflow-wrap: anywhere; }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
</style>
