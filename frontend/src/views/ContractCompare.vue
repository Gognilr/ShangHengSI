<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 合同对比（V35-D-07）：侧栏“文件审查”下的独立页面 /compare（V351-E-01），文件和结果只在本页，不留历史
import { computed, nextTick, onBeforeUnmount, ref, shallowRef } from 'vue'
import { ChevronLeft, ChevronRight, FileText, GitCompareArrows } from '@lucide/vue'
import { compareContracts, type ContractChange, type ContractComparison } from '@/api/contractCompare'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import { contractChangeLabel } from '@/utils/labels'

const firstFile = ref<HTMLButtonElement>()
const resultTitle = ref<HTMLElement>()
const list = ref<HTMLElement>()
const baselinePicker = ref<HTMLInputElement>()
const revisedPicker = ref<HTMLInputElement>()
const baseline = shallowRef<File | null>(null)
const revised = shallowRef<File | null>(null)
const names = ref({ baseline: '', revised: '' })
const result = shallowRef<ContractComparison | null>(null)
const selectedIndex = ref(0)
const error = ref('')
const busy = ref(false)
let request: AbortController | undefined

const changes = computed(() => result.value?.changes ?? [])
const selected = computed(() => changes.value[selectedIndex.value])
const evidence = computed(() => selected.value ? [
  { title: '基准稿', name: names.value.baseline, location: selected.value.baseline_location,
    segs: selected.value.segs.filter(seg => seg.t !== 'ins'), empty: '此处为修改稿新增' },
  { title: '修改稿', name: names.value.revised, location: selected.value.revised_location,
    segs: selected.value.segs.filter(seg => seg.t !== 'del'), empty: '修改稿已删除此处' },
] : [])

onBeforeUnmount(() => request?.abort())

function pick(event: Event, side: 'baseline' | 'revised') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const target = side === 'baseline' ? baseline : revised
  if (!/\.docx?$/i.test(file.name)) {
    target.value = null
    const label = side === 'baseline' ? '基准稿' : '修改稿'
    error.value = `${label}只支持 DOCX 和 DOC 文件，请重新选择。`
    return
  }
  target.value = file
  error.value = ''
}

async function start() {
  const old = baseline.value
  const current = revised.value
  if (!old || !current || busy.value) return
  const controller = new AbortController()
  request = controller
  busy.value = true
  error.value = ''
  try {
    const output = await compareContracts(old, current, controller.signal)
    if (controller.signal.aborted) return
    names.value = { baseline: old.name, revised: current.name }
    baseline.value = null
    revised.value = null
    selectedIndex.value = 0
    result.value = output
    await nextTick()
    resultTitle.value?.focus()
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof ApiError ? e.message : '对比失败，请重新选择文件后重试。'
  } finally {
    request = undefined
    busy.value = false
  }
}

async function restart() {
  result.value = null
  names.value = { baseline: '', revised: '' }
  error.value = ''
  await nextTick()
  firstFile.value?.focus()
}

function label(change: ContractChange) {
  return change.type === 'move' && change.segs.some(seg => seg.t !== 'eq')
    ? '位置与文字改动' : contractChangeLabel(change.type)
}

function move(offset: number) {
  selectedIndex.value += offset
  list.value?.children[selectedIndex.value]?.scrollIntoView({ block: 'nearest' })
}
</script>

<template>
  <section class="compare-workspace" :class="{ setup: !result }">
    <header class="tool-head">
      <div>
        <h1 class="entry-title">合同对比</h1>
        <p v-if="!result" class="entry-lede">在内网核对两份 Word，文件和结果用完即清。</p>
      </div>
      <el-button v-if="result?.changes.length" @click="restart">重新对比</el-button>
      <span v-else-if="busy" class="busy" role="status">正在本机对比</span>
    </header>

    <form v-if="!result" class="input-panel" @submit.prevent="start">
      <div class="pickers">
        <button ref="firstFile" type="button" class="file-slot" :disabled="busy" aria-label="选择基准稿" @click="baselinePicker?.click()">
          <span class="file-icon"><FileText :size="20" :stroke-width="1.75" /></span>
          <span class="file-label"><strong>基准稿</strong><span :title="baseline?.name">{{ baseline?.name ?? '选择原版 Word' }}</span></span>
        </button>
        <button type="button" class="file-slot" :disabled="busy" aria-label="选择修改稿" @click="revisedPicker?.click()">
          <span class="file-icon"><FileText :size="20" :stroke-width="1.75" /></span>
          <span class="file-label"><strong>修改稿</strong><span :title="revised?.name">{{ revised?.name ?? '选择要核对的 Word' }}</span></span>
        </button>
      </div>
      <input ref="baselinePicker" type="file" accept=".docx,.doc" hidden @change="pick($event, 'baseline')">
      <input ref="revisedPicker" type="file" accept=".docx,.doc" hidden @change="pick($event, 'revised')">
      <SiNotice v-if="error" class="error" type="error" :title="error" :closable="false" />
      <div class="form-actions">
        <p>支持 DOCX 和 DOC，对比正文和表格文字。</p>
        <el-button type="primary" native-type="submit" :disabled="busy || !baseline || !revised">开始对比</el-button>
      </div>
    </form>

    <div v-else class="result" aria-label="合同对比结果">
      <p class="coverage">正文和表格按接受已有修订后的文字对比。<template v-if="result.converted">DOC 已在本机转换，位置以转换后为准。</template></p>
      <div v-if="selected" class="comparison">
        <section class="change-list" aria-label="差异清单">
          <h2 ref="resultTitle" tabindex="-1">{{ changes.length }} 处差异</h2>
          <ol ref="list">
            <li v-for="(change, index) in changes" :key="change.id">
              <button type="button" class="change-row" :aria-pressed="index === selectedIndex" @click="selectedIndex = index">
                <span class="change-kind">{{ label(change) }}</span>
                <span class="change-position">{{ change.clause || change.revised_location || change.baseline_location }}</span>
                <span v-if="change.brief.text" class="excerpt">{{ change.brief.text }}</span>
                <span v-else class="excerpt">{{ change.brief.ctx }}<del v-if="change.brief.old">{{ change.brief.old }}</del><span v-if="change.brief.old && change.brief.new" class="arrow"> → </span><ins v-if="change.brief.new">{{ change.brief.new }}</ins></span>
              </button>
            </li>
          </ol>
        </section>
        <section class="evidence" aria-label="两稿原文对照">
          <header class="evidence-head">
            <h2>{{ selected.clause || label(selected) }}</h2>
            <div class="step">
              <el-button :icon="ChevronLeft" circle size="small" aria-label="上一处" title="上一处" :disabled="selectedIndex === 0" @click="move(-1)" />
              <span class="num" aria-live="polite">{{ selectedIndex + 1 }} / {{ changes.length }}</span>
              <el-button :icon="ChevronRight" circle size="small" aria-label="下一处" title="下一处" :disabled="selectedIndex >= changes.length - 1" @click="move(1)" />
            </div>
          </header>
          <div class="papers" :key="selected.id">
            <article v-for="side in evidence" :key="side.title" class="paper">
              <header class="paper-head">
                <h3>{{ side.title }}</h3>
                <span class="file-name" :title="side.name">{{ side.name }}</span>
                <span v-if="side.location" class="location">{{ side.location }}</span>
              </header>
              <p v-if="side.location" class="original"><template v-for="(seg, i) in side.segs" :key="i"><del v-if="seg.t === 'del'">{{ seg.s }}</del><ins v-else-if="seg.t === 'ins'">{{ seg.s }}</ins><span v-else>{{ seg.s }}</span></template></p>
              <p v-else class="absent">{{ side.empty }}</p>
            </article>
          </div>
        </section>
      </div>
      <section v-else ref="resultTitle" class="same" tabindex="-1">
        <EmptyState :icon="GitCompareArrows" title="未发现文字差异" description="两份稿件的正文和表格文字一致。">
          <el-button @click="restart">重新对比</el-button>
        </EmptyState>
      </section>
    </div>
  </section>
</template>

<style scoped>
.compare-workspace { max-width: 1344px; margin: 0 auto; padding: 40px 28px 64px; box-sizing: border-box; }
.compare-workspace.setup { max-width: 960px; }
.tool-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 28px; }
.tool-head .entry-lede { margin-bottom: 0; }
.busy { color: var(--si-blue-ink); font: var(--si-font-body); }
.input-panel { padding: 24px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.pickers, .papers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.file-slot { display: flex; align-items: center; gap: 16px; min-width: 0; min-height: 108px; padding: 20px;
  border: 1px dashed var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-card-2); color: var(--si-ink-1); text-align: left; cursor: pointer; }
.file-slot:hover:not(:disabled) { border-color: var(--si-brand); background: var(--si-hover-bg); }
.file-slot:disabled { cursor: default; }
.file-icon { display: grid; place-items: center; flex: none; width: 40px; height: 40px; border-radius: var(--si-radius-control); background: var(--si-info-soft); color: var(--si-info-ink); }
.file-label { display: grid; min-width: 0; gap: 4px; }
.file-label strong { font: var(--si-font-h3); }
.file-label span, .file-name { color: var(--si-ink-3); font: var(--si-font-body); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.form-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 20px; }
.form-actions p, .coverage { margin: 0; color: var(--si-ink-3); font: var(--si-font-body); }
.error { margin-top: 16px; }
.coverage { margin-bottom: 16px; }
.comparison { display: grid; grid-template-columns: minmax(224px, 264px) minmax(0, 1fr); gap: 16px; align-items: start; }
.change-list, .evidence, .same { min-width: 0; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.change-list h2, .evidence h2 { margin: 0; color: var(--si-ink-1); font: var(--si-font-h2); }
.change-list h2 { padding: 16px 16px 12px; }
.change-list ol { max-height: min(60vh, 640px); margin: 0; padding: 0 8px 8px; overflow: auto; list-style: none; }
.change-row { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; width: 100%; padding: 10px 8px; border: 1px solid transparent; border-radius: var(--si-radius-control); background: transparent; text-align: left; cursor: pointer; }
.change-row:hover { background: var(--si-hover-bg); }
.change-row[aria-pressed="true"] { background: var(--si-blue-tint); border-color: var(--si-brand-line); }
.change-kind { color: var(--si-ink-1); font: var(--si-font-h4); }
.change-position, .location, .step { color: var(--si-ink-3); font: var(--si-font-meta); }
.excerpt { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; color: var(--si-ink-2); font: var(--si-font-body); }
.arrow { color: var(--si-ink-3); }
.evidence-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
.step { display: inline-flex; flex: none; gap: 8px; align-items: center; }
.papers { padding: 12px; border-radius: 0 0 var(--si-radius-card) var(--si-radius-card); background: var(--si-sunken); }
.paper { min-width: 0; border-radius: var(--si-radius-paper); background: var(--si-paper); color: var(--si-paper-ink); box-shadow: var(--si-paper-shadow); }
.paper-head { display: grid; gap: 4px; padding: 14px 16px; border-bottom: 1px solid var(--si-hair); }
.paper h3 { margin: 0; font: var(--si-font-h3); }
.original, .absent { min-height: 120px; max-height: min(54vh, 560px); margin: 0; padding: 16px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-reading); }
.absent { color: var(--si-ink-3); }
.excerpt del, .original del { background: var(--si-danger-soft); color: var(--si-danger-ink); }
.excerpt ins, .original ins { background: var(--si-blue-tint); color: var(--si-blue-ink); text-underline-offset: 3px; }
.file-slot:focus-visible, .change-row:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.same { max-width: 960px; }
@media (max-width: 1100px) {
  .comparison { grid-template-columns: minmax(0, 1fr); }
  .change-list ol { max-height: 220px; }
}
@media (max-width: 680px) {
  .compare-workspace { padding: 24px 16px 40px; }
  .pickers, .papers { grid-template-columns: minmax(0, 1fr); }
  .form-actions { align-items: start; flex-direction: column; }
}
</style>
