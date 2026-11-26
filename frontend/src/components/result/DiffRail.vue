<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChevronDown, ChevronRight, Download, GitCompareArrows } from '@lucide/vue'
import { assistantApi, type DiffChange, type DiffResult } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { saveBlob } from '@/utils/save'
import EmptyState from '@/components/EmptyState.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { DIFF_TYPE, diffParts, traceText } from '@/utils/drafts'

/**
 * 右栏“变化”页签（V35-D-02，设计 diff.md 3.4）：对照上一稿的摘要、分段筛选、逐条变化；点一条在纸面切到对比并定位。
 * 格式变化（只改标点、空格或编号）折叠在最后。页签行与“意见”栏一致。
 */
const props = defineProps<{ sessionId: string; diff: DiffResult | null; loading: boolean; error: string; askTab?: boolean; selectedId?: string }>()
const emit = defineEmits<{ pick: [c: DiffChange]; findingsTab: []; askTab: [] }>()

type Filter = '' | 'u' | 'add' | 'del' | 'mod'
const filter = ref<Filter>('')
const formatOpen = ref(false)
watch(() => props.diff?.file_id, () => { filter.value = ''; formatOpen.value = false })
const main = computed(() => (props.diff?.changes ?? []).filter(c => c.type !== 'format'))
const formats = computed(() => (props.diff?.changes ?? []).filter(c => c.type === 'format'))
const match = (c: DiffChange, f: Filter) => !f || (f === 'u' ? !!c.untracked : f === 'mod' ? c.type === 'mod' || c.type === 'move' : c.type === f)
const options = computed(() => {
  const list = main.value
  const n = (f: Filter) => list.filter(c => match(c, f)).length
  const opts: { value: Filter; label: string }[] = [{ value: '', label: `全部 ${list.length}` }]
  if (props.diff?.trace !== 'none') opts.push({ value: 'u', label: `未留痕 ${n('u')}` })
  opts.push({ value: 'add', label: `新增 ${n('add')}` }, { value: 'del', label: `删除 ${n('del')}` }, { value: 'mod', label: `修改 ${n('mod')}` })
  return opts
})
const shown = computed(() => main.value.filter(c => match(c, filter.value)))
const parts = computed(() => props.diff ? diffParts(props.diff) : [])

const exporting = ref(false)
async function exportTable() {
  if (!props.diff || exporting.value) return
  exporting.value = true
  try {
    const { blob, filename } = await assistantApi.diffExport(props.sessionId, props.diff.file_id)
    if (await saveBlob(blob, filename) === 'downloaded') ElMessage.success('对照表已下载')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '导出失败，请稍后重试') }
  finally { exporting.value = false }
}
</script>

<template>
  <aside class="rail">
    <div class="rp-tabs">
      <button type="button" class="tab" @click="emit('findingsTab')">意见</button>
      <span class="tab on">变化<span v-if="diff" class="tab-n num">{{ main.length }}</span></span>
      <button v-if="askTab" type="button" class="tab" @click="emit('askTab')">追问</button>
      <el-button v-if="diff && main.length" class="export" link type="primary" size="small" :disabled="exporting" @click="exportTable"><Download :size="14" :stroke-width="1.75" />{{ exporting ? '正在生成…' : '导出对照表' }}</el-button>
    </div>
    <div v-if="loading" class="loading"><el-skeleton :rows="6" animated /></div>
    <SiNotice v-else-if="error" type="error" :closable="false" :title="error" class="alert" />
    <div v-else-if="diff" class="zone">
      <div class="sum">
        <span class="sum-ic"><GitCompareArrows :size="15" :stroke-width="1.75" /></span>
        <div class="sum-main">
          <b>对照第 {{ diff.prev_no }} 稿</b><span class="sum-name" :title="diff.prev_name">《{{ diff.prev_name }}》</span>
          <p v-if="parts.length" class="sum-line"><template v-for="(p, i) in parts" :key="p.text"><span v-if="i" class="dot">·</span><span :class="{ warn: p.warn }">{{ p.text }}</span></template></p>
          <p v-else class="sum-line">正文没有实质变化</p>
        </div>
      </div>
      <p v-if="diff.trace === 'flag'" class="note">本稿解析较早，只能按段落判断是否留痕。</p>
      <p v-if="diff.ocr" class="note">扫描件识字可能有误差，单字差异请对照原件。</p>
      <div v-if="main.length" class="filters"><SiSegmented v-model="filter" :options="options" size="small" ariaLabel="按变化类型筛选" /></div>
      <ul v-if="shown.length" class="list">
        <li v-for="c in shown" :key="c.id">
          <button type="button" class="row" :class="{ on: c.id === selectedId, u: c.untracked }" @click="emit('pick', c)">
            <span class="row-h">
              <span class="ty" :class="`ty-${c.type}`">{{ DIFF_TYPE[c.type] }}</span>
              <span class="clause">{{ c.clause || '—' }}</span>
              <span v-if="traceText(c)" class="tr" :class="{ 'tr-u': c.untracked }">{{ traceText(c) }}</span>
            </span>
            <span v-if="c.type === 'mod'" class="ex">{{ c.brief.ctx }}<del>{{ c.brief.old }}</del><span class="arrow">→</span><b>{{ c.brief.new }}</b></span>
            <span v-else class="ex" :class="{ gone: c.type === 'del' }">{{ c.type === 'move' && c.prev_clause ? `原${c.prev_clause}：` : '' }}{{ c.brief.text }}</span>
          </button>
        </li>
      </ul>
      <EmptyState v-else-if="main.length" title="没有符合筛选条件的变化" description="换一个筛选看看。" />
      <EmptyState v-else :icon="GitCompareArrows" :title="`与第 ${diff.prev_no} 稿正文一致`" description="两稿正文没有实质变化。" />
      <section v-if="formats.length" class="fmt">
        <button type="button" class="fmt-h" :aria-expanded="formatOpen" @click="formatOpen = !formatOpen">
          <component :is="formatOpen ? ChevronDown : ChevronRight" :size="14" :stroke-width="1.75" />格式变化 {{ formats.length }} 处<span class="hint">只改标点、空格或编号</span>
        </button>
        <ul v-if="formatOpen" class="list">
          <li v-for="c in formats" :key="c.id">
            <button type="button" class="row" :class="{ on: c.id === selectedId }" @click="emit('pick', c)">
              <span class="row-h"><span class="ty ty-format">格式</span><span class="clause">{{ c.clause || '—' }}</span></span>
              <span class="ex">{{ c.brief.ctx }}<del>{{ c.brief.old }}</del><span class="arrow">→</span><b>{{ c.brief.new }}</b></span>
            </button>
          </li>
        </ul>
      </section>
      <p class="foot hint">本机逐字对比，未发送给模型 · 页眉页脚、批注、文本框不在对比范围内</p>
    </div>
  </aside>
</template>

<style scoped>
.rail { display: flex; flex-direction: column; min-height: 0; height: 100%; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); overflow: hidden; }
.rp-tabs { flex: none; display: flex; align-items: center; gap: 18px; height: 48px; padding: 0 16px; border-bottom: 1px solid var(--si-hair-faint); }
.tab { position: relative; display: inline-flex; align-items: center; gap: 6px; height: 48px; padding: 0; border: 0; background: none; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; }
.tab:hover { color: var(--si-ink-1); }
.tab.on { color: var(--si-ink-1); font-weight: 600; cursor: default; }
.tab.on::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; border-radius: 2px; background: var(--si-brand-2); }
.tab-n { padding: 0 6px; border-radius: 999px; background: var(--si-surface-2); color: var(--si-ink-3); font: var(--si-font-label-sm); }
.export { margin-left: auto; }
.export :deep(> span) { gap: 4px; }
.loading { padding: 16px; }
.alert { margin: 12px 16px 0; width: auto; }
.zone { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 10px; }
.sum { display: flex; gap: 10px; padding: 10px 12px; border: 1px solid var(--si-hair-faint); border-radius: 12px; background: var(--si-card-2); }
.sum-ic { flex: none; display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px; background: var(--si-blue-tint); color: var(--si-brand-2); }
.sum-main { min-width: 0; flex: 1; font: var(--si-font-label); color: var(--si-ink-1); }
.sum-name { color: var(--si-ink-3); font: var(--si-font-label-sm); overflow-wrap: anywhere; }
.sum-line { margin: 4px 0 0; color: var(--si-ink-2); font: var(--si-font-body); }
.sum-line .dot { margin: 0 6px; color: var(--si-ink-4); }
.sum-line .warn { color: var(--si-warn-ink); font-weight: 600; }
.note { margin: 0; padding: 6px 10px; border-radius: 8px; background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-caption); }
.filters :deep(.si-segmented) { display: flex; width: 100%; }
.filters :deep(.si-segmented button) { flex: 1; }
.list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; flex-direction: column; gap: 4px; width: 100%; padding: 8px 10px 8px 12px; border: 1px solid var(--si-hair-faint); border-radius: 10px; background: var(--si-surface-1); box-shadow: inset 3px 0 0 var(--el-color-primary); text-align: left; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out), border-color var(--si-d2) var(--si-ease-out); }
.row.u { box-shadow: inset 3px 0 0 var(--si-warn); }
.row:hover { background: var(--si-hover-bg); }
.row.on { border-color: var(--el-color-primary); background: var(--si-blue-tint); }
.row:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.row-h { display: flex; align-items: center; gap: 8px; min-width: 0; }
.ty { flex: none; padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--si-surface-2); color: var(--si-ink-2); font: var(--si-font-label-sm); line-height: 20px; }
.ty-add { background: var(--si-blue-tint); color: var(--si-blue-ink); }
.ty-del { background: var(--si-surface-2); color: var(--si-ink-3); }
.clause { flex: 1; min-width: 0; overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.tr { flex: none; color: var(--si-ink-3); font: var(--si-font-caption); }
.tr-u { padding: 0 6px; border-radius: 999px; background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-badge); line-height: 20px; }
.ex { color: var(--si-ink-2); font: var(--si-font-body); overflow-wrap: anywhere; }
.ex del { color: var(--si-ink-3); }
.ex b { color: var(--si-ink-1); }
.ex .arrow { margin: 0 4px; color: var(--si-ink-4); }
.ex.gone { color: var(--si-ink-3); text-decoration: line-through; }
.fmt { border-top: 1px solid var(--si-hair-faint); padding-top: 6px; }
.fmt-h { display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 0; border: 0; background: none; color: var(--si-ink-2); font: var(--si-font-label); text-align: left; cursor: pointer; }
.fmt-h .hint { margin-left: 4px; }
.fmt .row { box-shadow: none; }
.foot { margin: 6px 0 0; }
</style>
