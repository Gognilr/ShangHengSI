<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronRight, ShieldCheck } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidGroupLabel, type BidModelView } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidStatus from '@/components/bid/BidStatus.vue'
import BidSentDrawer from './BidSentDrawer.vue'

// 模型补查（V35-E-01 改为可选）：只查 ★ 与否决条款中本机判断不了的；用户选择后才准备片段。
// “片段模式确认”只显示将发送的片段，按投标人分页签，可切 AI 视野；发送中显示“模型补查 n / N 家”，结果并入问题清单。
const props = defineProps<{ projectId: string; stateKey: string }>()
const emit = defineEmits<{ changed: [] }>()
const data = ref<BidModelView>(), error = ref(''), busy = ref(false), open = ref(false)
const sentOpen = ref(false)
const tab = ref(''), view = ref<'original' | 'ai'>('ai'), showReqs = ref(false)
const viewOptions: { label: string; value: 'original' | 'ai' }[] = [{ label: 'AI 视野', value: 'ai' }, { label: '原文', value: 'original' }]
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
    if (action === 'confirm') open.value = false
    emit('changed')
  } catch (e) { if (mounted) error.value = e instanceof ApiError ? e.message : '操作失败，请重试' }
  finally { busy.value = false; schedule() }
}
</script>

<template>
  <section v-if="data" class="model-check" aria-label="模型补查">
    <BidSentDrawer v-model="sentOpen" :project-id="projectId" />
    <div class="bar">
      <ShieldCheck :size="16" :stroke-width="1.75" aria-hidden="true" />
      <BidStatus v-if="ocrPending" tone="run" :label="`扫描页识别中 ${data.ocr!.done} / ${data.ocr!.total}，识别完成后可交模型补查`" />
      <template v-else-if="!data.state">
        <p>★ 与否决条款中本机判断不了的，可交模型补查（可选）：只发送各家与条款最相关的片段，脱敏后发送，投标人只以代号出现。</p>
        <el-button size="small" :loading="busy" @click="act('prepare')">准备模型补查</el-button>
      </template>
      <BidStatus v-else-if="data.state === 'preparing'" tone="run" label="正在本机检索相关片段并脱敏，可先看本地检查结果" />
      <template v-else-if="(data.state === 'awaiting' || data.state === 'failed') && data.receipt">
        <p>
          <span v-if="data.state === 'failed'" class="failed">模型补查未完成：{{ data.error }}。</span>
          将发送 <b class="num">{{ data.receipt.snippets }}</b> 个脱敏片段，约 <b class="num">{{ data.receipt.chars }}</b> 字，保护 <b class="num">{{ data.receipt.protected }}</b> 处。本次 <b class="num">{{ data.receipt.calls }}</b> 次调用。（{{ remainingText }}）
        </p>
        <div class="actions">
          <el-button size="small" text :aria-expanded="open" @click="open = !open">{{ open ? '收起片段' : '查看将发送的片段' }}</el-button>
          <el-button v-if="data.state === 'failed'" size="small" text :loading="busy" @click="act('prepare')">重新准备</el-button>
          <el-button v-else size="small" text :disabled="busy" @click="act('cancel')">不发送</el-button>
          <el-button size="small" :loading="busy" :disabled="notEnough" @click="act('confirm')">{{ retry ? '继续未完成的部分' : '确认并开始模型补查' }}</el-button>
        </div>
        <p v-if="notEnough" class="error">剩余调用次数不足，请联系管理员。</p>
      </template>
      <template v-else-if="data.state === 'failed'">
        <p class="failed">模型补查未完成：{{ data.error }}</p>
        <el-button size="small" :loading="busy" @click="act('prepare')">重新准备</el-button>
      </template>
      <BidStatus v-else-if="data.state === 'running'" tone="run" :label="`模型补查 ${data.progress.done} / ${data.progress.total} 家，结果随后补上`" />
      <template v-else-if="data.state === 'done'">
        <BidStatus tone="ok" :label="`模型补查已完成 ${data.progress.total} 家，结果已并入问题清单`" />
        <el-button class="again" size="small" text :loading="busy" @click="act('prepare')">重新准备</el-button>
      </template>
      <el-button v-if="(data.sent_count ?? 0) > 0 || ['done', 'running', 'failed'].includes(data.state ?? '')" text size="small" @click="sentOpen = true">查看实际发送内容</el-button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="open && data.receipt" class="notes">发送内容限于脱敏文字，原件、图片、报价金额和文件名留在本机。</p>
    <ul v-if="open && data.receipt?.notes.length" class="notes"><li v-for="note in data.receipt.notes" :key="note">{{ note }}</li></ul>

    <div v-if="open && current && (data.state === 'awaiting' || data.state === 'failed')" class="preview">
      <div class="preview-head">
        <SiSegmented v-model="tab" :options="tabs" size="small" ariaLabel="投标人" />
        <SiSegmented v-model="view" :options="viewOptions" size="small" ariaLabel="片段视图" />
      </div>
      <button type="button" class="si-overline toggle" :aria-expanded="showReqs" @click="showReqs = !showReqs">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showReqs }" aria-hidden="true" />一同发送的条款<span class="num">{{ data.requirements.length }}</span>
      </button>
      <ol v-if="showReqs" class="reqs">
        <li v-for="r in data.requirements" :key="r.id"><span class="code num">{{ r.n }}</span><span class="hint">{{ bidGroupLabel[r.group] }}</span><span>{{ view === 'ai' ? r.ai_text : r.text }}</span></li>
      </ol>
      <article v-for="s in current.snippets" :key="s.id" class="snippet">
        <header><span class="code num">{{ s.id }}</span><span class="hint">{{ view === 'ai' ? current.code : current.name }}<template v-if="view === 'original'"> · {{ s.file }}<template v-if="s.page"> 第 {{ s.page }} 页</template></template></span><span class="hint for">用于 {{ s.requirements.join('、') }}</span></header>
        <p class="text">{{ view === 'ai' ? s.ai_text : s.text }}</p>
      </article>
      <p v-if="!current.snippets.length" class="hint">没有检索到这家的相关片段。</p>
    </div>
  </section>
</template>

<style scoped>
.model-check { margin-bottom: 0; }
.bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; padding: 5px 0; border-top: 1px solid var(--si-hair); border-bottom: 1px solid var(--si-hair); font: var(--si-font-meta); color: var(--si-ink-2); }
.bar > svg { flex: none; color: var(--si-ink-3); }
.bar p { flex: 1; min-width: 240px; margin: 0; }
.actions, .again { margin-left: auto; }
.actions { display: flex; gap: 6px; }
.failed, .error { color: var(--si-danger-ink); }
.error { margin: 8px 0 0; font: var(--si-font-meta); }
.notes { margin: 10px 0 0; padding-left: 18px; font: var(--si-font-meta); color: var(--si-warn-ink); }
.preview { margin-top: 16px; display: flex; flex-direction: column; gap: 12px; }
.preview-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
.toggle { display: flex; align-items: center; gap: 8px; width: 100%; margin: 4px 0 0; padding: 0 0 8px; border: 0; border-bottom: 1px solid var(--si-hair-strong); background: transparent; cursor: pointer; text-align: left; }
.toggle .num { color: var(--si-ink-4); }
.chev { flex: none; color: var(--si-ink-4); transition: transform .15s; }
.turned { transform: rotate(90deg); }
.reqs { margin: 0; padding: 0; list-style: none; }
.reqs li { display: grid; grid-template-columns: 40px 120px minmax(0, 1fr); gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-1); }
.code { font: var(--si-font-code-sm); color: var(--si-ink-3); }
.snippet { padding: 10px 0 12px; border-bottom: 1px solid var(--si-hair-faint); }
.snippet header { display: flex; align-items: baseline; gap: 10px; }
.snippet .for { margin-left: auto; }
.text { margin: 6px 0 0; font: var(--si-font-body); color: var(--si-ink-1); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
