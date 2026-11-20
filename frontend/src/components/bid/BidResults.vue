<script setup lang="ts">
import { computed, onBeforeUnmount, provide, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCheck, type BidProject, type BidSigning, type BidSignMark } from '@/api/bid'
import BidIssues from './BidIssues.vue'
import BidBidderIssues from './BidBidderIssues.vue'
import BidClues from './BidClues.vue'
import { bidResultsKey, shortNames } from './results'
import './results.css'
import './charts/charts.css'

// 初审结果三个页签（V35-E-01）：问题清单｜各投标人｜串标线索。live 为真时（初审中）每 2 秒刷新，已完成的步骤先显示。
const props = defineProps<{ project: BidProject; stateKey: string; live?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const data = ref<BidCheck>(), error = ref(''), notice = ref('')
type Tab = 'issues' | 'bidders' | 'clues'
const tab = ref<Tab>('issues'), bidder = ref('')
const names = computed(() => Object.fromEntries(props.project.bidders.map(b => [b.id, b.name])))
const shorts = computed(() => shortNames(props.project.bidders))
const fileNames = computed(() => Object.fromEntries(props.project.files.map(f => [f.id, f.name])))
const renderable = computed(() => Object.fromEntries(props.project.files.map(f => [f.id, !f.original_deleted && ['pdf', 'jpg', 'jpeg', 'png'].includes(f.ext)])))

let timer: ReturnType<typeof setTimeout> | undefined, saving = 0, request = 0
async function load() {
  const current = ++request
  clearTimeout(timer)
  try {
    const next = await bidApi.check(props.project.id)
    if (current === request && !saving) { data.value = next; error.value = '' }
  } catch (e) { if (current === request) error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
  if (current === request && props.live) timer = setTimeout(() => void load(), 2000)
}
watch(() => [props.stateKey, props.live], () => void load(), { immediate: true })
onBeforeUnmount(() => { ++request; clearTimeout(timer) })

// “不是问题”：服务器重算结论后整体替换
async function dismiss(key: string, dismissed: boolean) {
  notice.value = ''; saving++
  try { data.value = await bidApi.dismiss(props.project.id, key, dismissed) }
  catch (e) { notice.value = e instanceof ApiError ? e.message : '没有保存，请重试' }
  finally { saving-- }
}
async function markSigning(pages: BidSigning[], mark: BidSignMark | null) {
  if (!pages.length) return
  const before = pages.map(s => [s, s.mark] as const)
  pages.forEach(s => { s.mark = mark }); notice.value = ''; saving++
  try {
    if (pages.length === 1) await bidApi.sign(props.project.id, pages[0]!, mark)
    else await bidApi.signBatch(props.project.id, pages.map(s => ({ file_id: s.file_id, page: s.page, loc: s.loc })), mark)
    data.value = await bidApi.check(props.project.id)  // 缺章是严重问题，结论随之更新
  } catch (e) {
    before.forEach(([s, m]) => { s.mark = m })
    notice.value = e instanceof ApiError ? e.message : '标注没有保存，已恢复，请重试'
  } finally { saving-- }
}

// 查看原件页：大图对话框，可翻页
const page = ref<{ fileId: string; page: number } | null>(null)
const pageCount = computed(() => props.project.files.find(f => f.id === page.value?.fileId)?.page_count ?? 1)
function openPage(fileId: string, n: number) { page.value = { fileId, page: n } }
provide(bidResultsKey, { projectId: props.project.id, data, names, shorts, fileNames, renderable, dismiss, markSigning, openPage })

// 各投标人默认选严重问题最多的一家
watch(data, d => {
  if (bidder.value || !d) return
  bidder.value = [...d.conclusion.bidders].sort((a, b) => b.severe - a.severe || b.hint - a.hint)[0]?.id ?? props.project.bidders[0]?.id ?? ''
})
const tabs = computed(() => [
  { key: 'issues' as Tab, label: '问题清单', n: data.value?.conclusion.severe.length },
  { key: 'bidders' as Tab, label: '各投标人' },
  { key: 'clues' as Tab, label: '串标线索', n: data.value?.conclusion.cases.length },
])
const declaration = '系统只指出问题并定位原件，不作否决或串通投标的认定。'
const notes = computed(() => [...(data.value?.coverage ?? []), '没有列出的事项，不等于投标文件没有问题。'])
</script>

<template>
  <div class="results">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <el-alert v-if="notice" class="notice" :title="notice" type="warning" show-icon @close="notice = ''" />
    <nav class="tabs" role="tablist" aria-label="初审结果">
      <button v-for="t in tabs" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" :class="{ on: tab === t.key }" @click="tab = t.key">{{ t.label }}<i v-if="t.n" class="num">{{ t.n }}</i></button>
    </nav>
    <template v-if="data">
      <BidIssues v-if="tab === 'issues'" />
      <BidBidderIssues v-else-if="tab === 'bidders'" v-model="bidder" :bidders="project.bidders" @renamed="emit('changed')" />
      <BidClues v-else />
    </template>
    <p v-else-if="!error && !data" class="hint" role="status">正在读取初审结果…</p>
    <ul class="bid-foot foot"><li v-for="n in notes" :key="n">{{ n }}</li><li>{{ declaration }}</li></ul>

    <el-dialog :model-value="!!page" width="min(920px, 92vw)" append-to-body :title="page ? `${fileNames[page.fileId] ?? '原件'} · 第 ${page.page} 页` : ''" @update:model-value="(v: boolean) => { if (!v) page = null }">
      <div v-if="page" class="page-view">
        <img :src="bidApi.pageUrl(project.id, page.fileId, page.page, 1100)" :alt="`第 ${page.page} 页`">
      </div>
      <template #footer>
        <div v-if="page" class="pager">
          <el-button :disabled="page.page <= 1" @click="page.page--"><ChevronLeft :size="16" :stroke-width="1.75" />上一页</el-button>
          <span class="num">{{ page.page }} / {{ pageCount }}</span>
          <el-button :disabled="page.page >= pageCount" @click="page.page++">下一页<ChevronRight :size="16" :stroke-width="1.75" /></el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.results { flex: 1; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.foot { margin-top: auto; padding-top: 14px; }
.tabs { display: flex; gap: 26px; border-bottom: 1px solid var(--si-hair); }
.tabs button { position: relative; height: 36px; padding: 0; border: 0; background: transparent; font: var(--si-font-label); color: var(--si-ink-3); cursor: pointer; }
.tabs button:hover { color: var(--si-ink-1); }
.tabs button.on { color: var(--si-ink-1); font-weight: 600; }
.tabs button.on::after { content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--si-ink-1); }
.tabs i { margin-left: 5px; font: var(--si-font-code-sm); font-style: normal; color: var(--si-ink-4); }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.error { margin: 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
.page-view { max-height: 70vh; overflow: auto; background: var(--si-stage); border-radius: var(--si-radius-card); }
.page-view img { display: block; width: 100%; }
.pager { display: flex; align-items: center; justify-content: center; gap: 16px; }
.pager .el-button svg { margin: 0 4px; }
</style>
