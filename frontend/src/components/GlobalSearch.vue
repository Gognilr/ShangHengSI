<script setup lang="ts">
import { computed, nextTick, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { FileSearch, FilePlus, History as HistoryIcon, MessageSquare, MessageSquarePlus, Search, Settings } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import { searchApi, type SearchResult } from '@/api/search'
import { useAuthStore } from '@/stores/auth'
import { formatDate } from '@/utils/time'

// 全局搜索（V3-A 6.2）：操作、页面在本地过滤；审核与会话来自接口（只含本人任务）
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ 'new-session': [] }>()
const router = useRouter()
const auth = useAuthStore()

interface Entry { key: string; group: string; label: string; icon: Component; meta?: string; run: () => void }

const query = ref('')
const result = ref<SearchResult>({ reviews: [], sessions: [] })
const searching = ref(false)
const active = ref(0)
const input = ref<{ focus: () => void } | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let version = 0

const staticEntries = computed<Entry[]>(() => {
  const go = (path: string) => () => void router.push(path)
  const list: Entry[] = [
    { key: 'a-review', group: '操作', label: '新建审核', icon: FilePlus, run: go('/review/new') },
    { key: 'a-session', group: '操作', label: '新建会话', icon: MessageSquarePlus, run: () => emit('new-session') },
    { key: 'p-assistant', group: '页面', label: '智能助手', icon: MessageSquare, run: go('/assistant') },
    { key: 'p-review', group: '页面', label: '智能审核', icon: FileSearch, run: go('/review/new') },
    { key: 'p-history', group: '页面', label: '审核历史', icon: HistoryIcon, run: go('/history') },
  ]
  const settings = [
    { name: 'personal', label: '个人（外观、修改密码）', scope: 'all' }, { name: 'users', label: '账号', scope: 'sys' }, { name: 'model', label: '模型', scope: 'sys' },
    { name: 'requirements', label: '审核要求', scope: 'biz' }, { name: 'laws', label: '法规库', scope: 'biz' },
    { name: 'templates', label: '公司范本', scope: 'biz' }, { name: 'knowledge', label: '知识库', scope: 'biz' },
    { name: 'redaction', label: '脱敏', scope: 'biz' }, { name: 'data', label: '数据', scope: 'sys' },
  ]
  for (const s of settings) {
    if (s.scope === 'all' || (s.scope === 'sys' ? auth.canSysAdmin : auth.canBizAdmin)) list.push({ key: `p-set-${s.name}`, group: '页面', label: `设置 · ${s.label}`, icon: Settings, run: go(`/settings/${s.name}`) })
  }
  return list
})

const entries = computed<Entry[]>(() => {
  const q = query.value.trim().toLowerCase()
  const local = staticEntries.value.filter(e => !q || e.label.toLowerCase().includes(q))
  const reviews: Entry[] = result.value.reviews.map(r => ({ key: `r-${r.id}`, group: '审核', label: r.title, icon: FileSearch, meta: formatDate(r.created_at), run: () => void router.push({ name: 'review-result', params: { taskId: r.id } }) }))
  const sessions: Entry[] = result.value.sessions.map(s => ({ key: `s-${s.id}`, group: '会话', label: s.title, icon: MessageSquare, meta: formatDate(s.last_activity_at), run: () => void router.push(`/assistant/${s.id}`) }))
  return [...local.filter(e => e.group === '操作'), ...local.filter(e => e.group === '页面'), ...reviews, ...sessions]
})
const groups = computed(() => {
  const out: { name: string; items: Entry[] }[] = []
  for (const entry of entries.value) {
    const last = out[out.length - 1]
    if (last && last.name === entry.group) last.items.push(entry)
    else out.push({ name: entry.group, items: [entry] })
  }
  return out
})
const noResult = computed(() => query.value.trim().length > 0 && !searching.value && !entries.value.length)

watch(open, value => {
  if (value) { query.value = ''; result.value = { reviews: [], sessions: [] }; active.value = 0; void nextTick(() => input.value?.focus()) }
  else { clearTimeout(timer); ++version }
})
watch(query, value => {
  clearTimeout(timer)
  const q = value.trim()
  active.value = 0
  const current = ++version
  if (!q) { result.value = { reviews: [], sessions: [] }; searching.value = false; return }
  searching.value = true
  timer = setTimeout(async () => {
    try {
      const data = await searchApi.search(q.slice(0, 50))
      if (current === version) result.value = data
    } catch { if (current === version) result.value = { reviews: [], sessions: [] } }
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
</script>

<template>
  <el-dialog v-model="open" class="global-search" width="640px" top="15vh" :show-close="false" destroy-on-close append-to-body>
    <template #header><span class="sr-only">搜索</span></template>
    <el-input ref="input" v-model="query" size="large" placeholder="搜索审核、会话、页面或操作" aria-label="搜索" maxlength="50" @keydown="onKey">
      <template #prefix><Search :size="16" :stroke-width="1.75" /></template>
    </el-input>
    <div class="results" role="listbox" aria-label="搜索结果">
      <template v-if="!noResult">
        <div v-for="g in groups" :key="g.name" class="group">
          <div class="group-title">{{ g.name }}</div>
          <div v-for="entry in g.items" :key="entry.key" class="entry" :class="{ active: indexOf(entry) === active }" role="option" :aria-selected="indexOf(entry) === active" @mouseenter="active = indexOf(entry)" @click="choose(entry)">
            <component :is="entry.icon" :size="16" :stroke-width="1.75" />
            <span class="label">{{ entry.label }}</span>
            <span v-if="entry.meta" class="meta">{{ entry.meta }}</span>
          </div>
        </div>
      </template>
      <EmptyState v-else compact title="没有找到匹配的审核或会话" description="换个关键词再试，只搜索你自己的审核和会话标题。" />
    </div>
  </el-dialog>
</template>

<style>
/* 对话框内容被 teleport 到 body，样式不加 scoped */
.global-search.el-dialog { padding: 16px; }
.global-search .el-dialog__header { display: none; }
.global-search .results { margin-top: 12px; max-height: 50vh; overflow-y: auto; }
.global-search .group + .group { margin-top: 8px; }
.global-search .group-title { padding: 4px 12px; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.global-search .entry { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: var(--el-border-radius-base); cursor: pointer; font: var(--si-font-body); }
.global-search .entry.active { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.global-search .entry .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.global-search .entry .meta { flex: none; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
