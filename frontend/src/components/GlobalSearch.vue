<script setup lang="ts">
import { SETTING_TABS } from '@/utils/permissions'
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { AlertCircle, ClockFading, CornerDownLeft, FilePlus, FileSearch, FileStack, House, Languages, MessageSquare, MessageSquarePlus, Mic, Search, Settings } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import { isReviewSession, sessionLocation } from '@/api/assistant'
import { searchApi, type SearchResult } from '@/api/search'
import { useAuthStore } from '@/stores/auth'
import { formatDate } from '@/utils/time'

// 全局搜索（V3-A 6.2）：操作、页面在本地过滤；审核、标书初审与会话来自接口（只含本人任务）
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ 'new-session': [] }>()
const router = useRouter()
const auth = useAuthStore()

/** mod：模块色（澄明 8.0 结果按模块分组，图标底块用模块色） */
interface Entry { key: string; group: string; label: string; icon: Component; mod?: string; meta?: string; run: () => void }

const query = ref('')
const result = ref<SearchResult>({ reviews: [], sessions: [], findings: [], bids: [] })
const searching = ref(false)
const active = ref(0)
const input = ref<HTMLInputElement | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let version = 0

const staticEntries = computed<Entry[]>(() => {
  const go = (path: string) => () => void router.push(path)
  const list: Entry[] = [
    ...(auth.can('use.review') ? [{ key: 'a-review', group: '操作', label: '新建审核', icon: FilePlus, mod: 'review', run: go('/review') }] : []),
    ...(auth.can('use.bid') ? [{ key: 'a-bid', group: '操作', label: '新建标书初审', icon: FileStack, mod: 'bid', run: () => void router.push({ path: '/bid', query: { new: '1' } }) }] : []),
    ...(auth.can('use.assistant') ? [{ key: 'a-session', group: '操作', label: '新建会话', icon: MessageSquarePlus, run: () => emit('new-session') },
      { key: 'p-assistant', group: '页面', label: '工作台', icon: House, run: go('/assistant') }] : []),
    ...(auth.can('use.review') ? [{ key: 'p-review', group: '页面', label: '文件审核', icon: FileSearch, mod: 'review', run: go('/review') }] : []),
    ...(auth.can('use.bid') ? [{ key: 'p-bid', group: '页面', label: '标书初审', icon: FileStack, mod: 'bid', run: go('/bid') }] : []),
    ...(auth.can('use.meeting') ? [{ key: 'p-meeting', group: '页面', label: '会议纪要', icon: Mic, mod: 'meet', run: go('/meeting') }] : []),
    { key: 'p-translate', group: '页面', label: '翻译', icon: Languages, mod: 'trans', run: go('/translate') },
    { key: 'p-history', group: '页面', label: '历史记录', icon: ClockFading, run: go('/history') },
  ]
  for (const s of SETTING_TABS) {
    if (auth.can(...s.perms)) list.push({ key: `p-set-${s.name}`, group: '页面', label: `设置 · ${s.search ?? s.label}`, icon: Settings, run: go(`/settings/${s.name}`) })
  }
  return list
})

const entries = computed<Entry[]>(() => {
  const q = query.value.trim().toLowerCase()
  const local = staticEntries.value.filter(e => !q || e.label.toLowerCase().includes(q))
  const reviews: Entry[] = result.value.reviews.map(r => ({ key: `r-${r.id}`, group: '文件审核', label: r.title, icon: FileSearch, mod: 'review', meta: formatDate(r.created_at), run: () => void router.push({ name: 'review-result', params: { taskId: r.id } }) }))
  const bids: Entry[] = result.value.bids.map(b => ({ key: `b-${b.id}`, group: '标书初审', label: b.title, icon: FileStack, mod: 'bid', meta: formatDate(b.last_activity_at), run: () => void router.push(`/bid/${b.id}`) }))
  const sessions: Entry[] = result.value.sessions.map(s => {
    const [group, icon, mod] = s.purpose === 'meeting' ? ['会议纪要', Mic, 'meet'] as const
      : s.purpose === 'translate' ? ['翻译', Languages, 'trans'] as const
      : auth.can('use.review') && isReviewSession(s) ? ['文件审核', FileSearch, 'review'] as const : ['对话', MessageSquare, undefined] as const
    return { key: `s-${s.id}`, group, label: s.title, icon, mod, meta: formatDate(s.last_activity_at),
      run: () => void router.push(sessionLocation(s, auth.can('use.review'))) }
  })
  const findings: Entry[] = result.value.findings.map(f => ({ key: `f-${f.id}`, group: '问题', label: f.title, icon: AlertCircle, mod: 'review', meta: f.task_title, run: () => void router.push(f.kind === 'review' ? { name: 'review-result', params: { taskId: f.task_id }, query: { finding: f.id } } : { path: `/assistant/${f.task_id}`, query: { mode: 'review' } }) }))
  return [...local.filter(e => e.group === '操作'), ...local.filter(e => e.group === '页面'), ...reviews, ...bids, ...findings,
    ...['文件审核', '对话', '会议纪要', '翻译'].flatMap(group => sessions.filter(s => s.group === group))]
})
const groups = computed(() => {
  const out: { name: string; items: Entry[] }[] = []
  for (const entry of entries.value) {
    const group = out[out.length - 1]
    if (group?.name === entry.group) group.items.push(entry)
    else out.push({ name: entry.group, items: [entry] })
  }
  return out
})
const noResult = computed(() => query.value.trim().length > 0 && !searching.value && !entries.value.length)

watch(open, value => {
  if (value) { query.value = ''; result.value = { reviews: [], sessions: [], findings: [], bids: [] }; active.value = 0; void nextTick(() => input.value?.focus()) }
  else { clearTimeout(timer); ++version }
})
watch(query, value => {
  clearTimeout(timer)
  const q = value.trim()
  active.value = 0
  const current = ++version
  if (!q) { result.value = { reviews: [], sessions: [], findings: [], bids: [] }; searching.value = false; return }
  searching.value = true
  timer = setTimeout(async () => {
    try {
      const data = await searchApi.search(q.slice(0, 50))
      if (current === version) result.value = data
    } catch { if (current === version) result.value = { reviews: [], sessions: [], findings: [], bids: [] } }
    finally { if (current === version) searching.value = false }
  }, 300)
})

function choose(entry: Entry | undefined) {
  if (!entry) return
  open.value = false
  entry.run()
}
function onKey(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  const count = entries.value.length
  if (event.key === 'ArrowDown') { event.preventDefault(); if (count) active.value = (active.value + 1) % count }
  else if (event.key === 'ArrowUp') { event.preventDefault(); if (count) active.value = (active.value - 1 + count) % count }
  else if (event.key === 'Enter') { event.preventDefault(); choose(entries.value[active.value]) }
}
watch(active, () => void nextTick(() => document.querySelector('.global-search .entry.active')?.scrollIntoView({ block: 'nearest' })))
function indexOf(entry: Entry) { return entries.value.findIndex(e => e.key === entry.key) }
// 标题里命中的字用琥珀浅底标出（不区分大小写，只标第一处）
function parts(label: string): [string, string, string] {
  const q = query.value.trim(), at = q ? label.toLowerCase().indexOf(q.toLowerCase()) : -1
  return at < 0 ? [label, '', ''] : [label.slice(0, at), label.slice(at, at + q.length), label.slice(at + q.length)]
}
</script>

<template>
  <el-dialog v-model="open" class="global-search" width="640px" top="15vh" :show-close="false" destroy-on-close append-to-body>
    <template #header><span class="sr-only">搜索</span></template>
    <div class="gs-in">
      <Search :size="20" :stroke-width="1.75" />
      <input ref="input" v-model="query" placeholder="搜索审核、标书、问题、会话、页面或操作" aria-label="搜索" maxlength="50" @keydown="onKey">
      <kbd class="si-kbd">Esc</kbd>
    </div>
    <div class="results" role="listbox" aria-label="搜索结果">
      <template v-if="!noResult">
        <div v-for="g in groups" :key="g.name" class="group">
          <div class="group-title">{{ g.name }}</div>
          <div v-for="entry in g.items" :key="entry.key" class="entry" :class="[{ active: indexOf(entry) === active }, entry.mod ? `m-${entry.mod}` : '']" role="option" :aria-selected="indexOf(entry) === active" @mouseenter="active = indexOf(entry)" @click="choose(entry)">
            <span class="ic"><component :is="entry.icon" :size="16" :stroke-width="1.75" /></span>
            <span class="label">{{ parts(entry.label)[0] }}<mark v-if="parts(entry.label)[1]">{{ parts(entry.label)[1] }}</mark>{{ parts(entry.label)[2] }}</span>
            <span v-if="entry.meta" class="meta">{{ entry.meta }}</span>
            <CornerDownLeft v-if="indexOf(entry) === active" class="enter" :size="16" :stroke-width="1.75" aria-hidden="true" />
          </div>
        </div>
      </template>
      <EmptyState v-else compact :icon="Search" title="没有找到匹配的内容" description="可搜审核、标书项目与会话标题、问题标题和描述（按主题词，如“违约金”；真实名称和金额搜不到）。" />
    </div>
    <div class="gs-foot"><span><kbd class="si-kbd">↑</kbd> <kbd class="si-kbd">↓</kbd> 选择</span><span><kbd class="si-kbd">Enter</kbd> 打开</span><span class="mine">只搜索你自己的记录</span></div>
  </el-dialog>
</template>

<style>
/* 对话框内容被 teleport 到 body，样式不加 scoped（澄明 8.0：居中浮层，宽 640，结果按模块分组） */
.global-search.el-dialog { padding: 0; overflow: hidden; }
html[data-fx="full"] .global-search.el-dialog { background: var(--si-glass); backdrop-filter: blur(20px) saturate(1.4); }
.global-search .el-dialog__header { display: none; }
.global-search .el-dialog__body { padding: 0; }
.global-search .gs-in { display: flex; align-items: center; gap: 12px; height: 60px; padding: 0 20px; border-bottom: 1px solid var(--si-hair); color: var(--si-ink-3); }
.global-search .gs-in input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--si-ink-1); font: var(--si-font-body); font-size: calc(17px * var(--si-fs)); }
.global-search .gs-in input::placeholder { color: var(--si-ink-4); }
.global-search .results { padding: 8px; max-height: min(420px, 50vh); overflow-y: auto; }
.global-search .group-title { padding: 10px 12px 6px; font: var(--si-font-caption); font-weight: 500; color: var(--si-ink-4); }
.global-search .entry { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-radius: 10px; color: var(--si-ink-1); font: var(--si-font-body); cursor: pointer; }
.global-search .entry.active { background: var(--si-select-bg); }
.global-search .entry .ic { flex: none; display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: var(--mc-soft, var(--si-blue-tint)); color: var(--mc, var(--si-brand-2)); }
.global-search .m-review { --mc: var(--si-m-review); --mc-soft: var(--si-m-review-soft); }
.global-search .m-bid { --mc: var(--si-m-bid); --mc-soft: var(--si-m-bid-soft); }
.global-search .m-meet { --mc: var(--si-m-meet); --mc-soft: var(--si-m-meet-soft); }
.global-search .m-trans { --mc: var(--si-m-trans); --mc-soft: var(--si-m-trans-soft); }
.global-search .entry .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.global-search .entry mark { padding: 0 1px; border-radius: 3px; background: var(--si-warn-soft); color: inherit; }
.global-search .entry .meta { flex: none; font: var(--si-font-meta); color: var(--si-ink-3); }
.global-search .entry .enter { flex: none; color: var(--si-ink-4); }
.global-search .gs-foot { display: flex; align-items: center; gap: 16px; padding: 10px 20px; border-top: 1px solid var(--si-hair-faint); font: var(--si-font-meta); color: var(--si-ink-3); }
.global-search .gs-foot .mine { margin-left: auto; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
