<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { reportApi } from '@/api/report'
import { assistantApi, type HistoryItem } from '@/api/assistant'
import { bidApi, bidState } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { useAuthStore } from '@/stores/auth'
import { tasksApi } from '@/api/tasks'
import EmptyState from '@/components/EmptyState.vue'
import RowMore from '@/components/RowMore.vue'
import TableSkeleton from '@/components/TableSkeleton.vue'
import { docTypeLabel, stanceLabel, statusLabel, statusTag } from '@/utils/labels'
import type { SaveResult } from '@/utils/save'
import { useTableMaxHeight } from '@/utils/tableHeight'
import { formatDate, formatDateTimeMinute } from '@/utils/time'
import { FileSearch, MessageSquare, Scale, Search } from '@lucide/vue'

const router = useRouter(), auth = useAuthStore()
const tableMax = useTableMaxHeight(300)
function fileBadge(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return ['docx', 'doc', 'wps', 'rtf'].includes(ext) ? 'DOC' : ['xlsx', 'xls', 'et'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'IMG'
}

const items = ref<HistoryItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const initialLoad = ref(true)
const error = ref('')
const exporting = ref('')

async function exportReport(row: HistoryItem) {
  if (exporting.value) return
  exporting.value = row.id
  try {
    // 会话与旧审核任务一样：导出最新一轮的报告；仅本地校验的旧任务导出本地报告；标书初审导出初审疑点报告
    const result: SaveResult = row.kind === 'bid' ? await reportApi.bid(row.id)
      : row.status === 'completed_local' && row.kind === 'review' ? await reportApi.local(row.id)
      : row.latest_run_id ? await reportApi.run(row.latest_run_id) : 'cancelled'
    if (result === 'downloaded') ElMessage.success('报告已下载')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '导出失败，请稍后重试')
  } finally { exporting.value = '' }
}
const canExport = (row: HistoryItem) => row.kind === 'bid' ? row.stage === 'checked' && auth.can('use.export')
  : !row.reviewing && (!!row.latest_run_id || (row.kind === 'review' && row.status === 'completed_local'))
const hasRun = (row: HistoryItem) => row.kind === 'bid' ? row.stage === 'checked' : row.kind === 'review' ? row.status === 'reviewed' : row.run_count > 0

// 类型分段（V34-B-07）：标书初审只有有权限的账号才有
type Kind = 'all' | 'review' | 'bid' | 'chat'
const kind = ref<Kind>('all')
const kindOptions = computed<{ value: Kind; label: string }[]>(() => [
  { value: 'all', label: '全部' }, { value: 'review', label: '文件审核' },
  ...(auth.can('use.bid') ? [{ value: 'bid' as Kind, label: '标书初审' }] : []), { value: 'chat', label: '对话' },
])

const q = ref('')
// 日期范围：YYYY-MM-DD（按 Asia/Shanghai 日界，由后端换算）
const range = ref<[string, string] | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await assistantApi.history({
      page: page.value,
      q: q.value.trim() || undefined,
      from: range.value?.[0],
      to: range.value?.[1],
      type: kind.value === 'all' ? undefined : kind.value,
    })
    items.value = res.items
    total.value = res.total
    pageSize.value = res.page_size
    // 删除后当前页已超出范围时回到最后一页
    if (!res.items.length && res.total > 0 && page.value > 1) {
      page.value = Math.ceil(res.total / res.page_size)
      await load()
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请刷新页面'
  } finally {
    loading.value = false
    initialLoad.value = false
  }
}

function search() {
  page.value = 1
  void load()
}
watch(kind, search)
// 输入即筛选：停顿 300ms 后查询；回车、清除、选日期立即查询
let debounce: ReturnType<typeof setTimeout> | undefined
function typing() { clearTimeout(debounce); debounce = setTimeout(search, 300) }
onBeforeUnmount(() => clearTimeout(debounce))

/** 名称第二行小字：类型 · 文件类型 · 立场（V34-A-05，原三列并入名称） */
function subline(row: HistoryItem) {
  if (row.kind === 'bid') return ['标书初审', `${row.file_count} 个文件`].join(' · ')
  const parts = [row.kind === 'session' ? '会话' : '审核任务']
  if (row.doc_type) parts.push(docTypeLabel(row.doc_type))
  if (row.kind === 'review' || row.run_count) parts.push(`${stanceLabel(row.stance)}立场`)
  return parts.join(' · ')
}
/** 状态：圆点 + 文字（V34-A-05） */
function statusView(row: HistoryItem): { text: string; tone: string } {
  if (row.kind === 'bid') {
    const s = bidState({ stage: row.stage as 'preparing', status: row.status })
    return { text: s.label, tone: { run: 'busy', warn: 'warning', ok: 'success', idle: 'info' }[s.tone] }
  }
  if (row.reviewing) return { text: '审核中', tone: 'busy' }
  if (row.kind === 'session') return { text: row.run_count ? `已审核 ${row.run_count} 轮` : '对话', tone: row.run_count ? 'primary' : 'info' }
  return { text: statusLabel(row.status), tone: statusTag(row.status).type }
}

function open(row: HistoryItem) {
  // 工作台会话回到工作台（发起过审核的直接看审核视图）；旧审核任务用原页面打开，未完成的由结果页转到对应步骤（V32-B-01）
  if (row.kind === 'bid') { void router.push(`/bid/${row.id}`); return }
  if (row.kind === 'session') { void router.push({ path: `/assistant/${row.id}`, query: row.run_count ? { mode: 'review' } : {} }); return }
  void router.push(row.status === 'draft' ? { name: 'review-new', query: { task: row.id } } : { name: 'review-result', params: { taskId: row.id } })
}

async function remove(row: HistoryItem) {
  const session = row.kind === 'session', bid = row.kind === 'bid'
  try {
    await ElMessageBox.confirm(
      bid ? `将删除“${row.title}”的全部原件、密码与记录，无法恢复。` : session ? `将删除“${row.title}”及其文件、消息、对照表和审核结果，无法恢复。` : `确定彻底删除“${row.title}”吗？原件、脱敏记录和审核结果都会被删除，无法恢复。`,
      bid ? '删除初审项目' : session ? '删除会话' : '删除审核任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await (bid ? bidApi.remove(row.id) : session ? assistantApi.remove(row.id) : tasksApi.remove(row.id))
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '删除失败，请重试')
  }
}

const openLabel = (row: HistoryItem) => row.kind !== 'bid' && row.status === 'draft' ? '继续' : '打开'
function expiring(value: string | null) { if (!value) return false; const remaining = new Date(value).getTime() - Date.now(); return remaining >= 0 && remaining <= 7 * 86400000 }
onMounted(load)
</script>

<template>
  <div class="page page--wide history-page">
    <header class="head"><h1 class="entry-title">历史记录</h1><span class="hint">共 {{ total }} 条 · 仅本人可见</span></header>
    <div class="filters">
      <el-input v-model="q" aria-label="按文件名搜索" clearable placeholder="按文件名搜索" class="q" @input="typing" @keyup.enter="search" @clear="search"><template #prefix><Search :size="16" :stroke-width="1.75" /></template></el-input>
      <SiSegmented v-model="kind" :options="kindOptions" ariaLabel="类型" />
      <el-date-picker
        v-model="range"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        @change="search"
      />
    </div>

    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" class="gap" />

    <TableSkeleton v-if="loading && initialLoad && !items.length" :columns="['1fr', '150px', '68px', '68px', '148px', '104px', '92px']" />
    <el-table v-else v-loading="loading" :data="items" :max-height="tableMax" class="history-table">
      <template #empty><EmptyState :icon="FileSearch" title="没有历史记录" description="在工作台提问或完成审核后，记录会出现在这里。"><el-button @click="router.push('/assistant')">去工作台</el-button></EmptyState></template>
      <el-table-column prop="title" label="名称" min-width="300"><template #default="{ row }"><div class="file-cell"><span v-if="row.kind === 'review'" class="file-badge">{{ fileBadge(row.title) }}</span><span v-else class="file-badge"><Scale v-if="row.kind === 'bid'" :size="16" :stroke-width="1.75" /><MessageSquare v-else :size="16" :stroke-width="1.75" /></span><span class="file-text"><span class="file-title" :title="row.title">{{ row.title }}</span><span class="file-sub">{{ subline(row) }}</span></span></div></template></el-table-column>
      <el-table-column label="状态" width="150">
        <template #default="{ row }"><span class="status" :class="`tone-${statusView(row).tone}`"><i class="dot" />{{ statusView(row).text }}</span></template>
      </el-table-column>
      <el-table-column label="高风险" width="68" align="right"><template #default="{ row }"><span class="num" :class="row.high_count > 0 && hasRun(row) ? 'high-count' : 'zero'">{{ hasRun(row) ? row.high_count : '—' }}</span></template></el-table-column>
      <el-table-column label="待处理" width="68" align="right"><template #default="{ row }"><span class="num" :class="row.pending_count > 0 && hasRun(row) ? 'pending-count' : 'zero'">{{ hasRun(row) ? row.pending_count : '—' }}</span></template></el-table-column>
      <el-table-column label="最近活动" width="148"><template #default="{ row }"><span class="num">{{ formatDateTimeMinute(row.last_activity_at) }}</span></template></el-table-column>
      <el-table-column label="保留至" width="104"><template #default="{ row }"><span class="num" :class="{ expiring: expiring(row.task_expires_at) }">{{ row.task_expires_at ? formatDate(row.task_expires_at) : row.kind === 'session' || row.kind === 'bid' ? '—' : '永久' }}</span></template></el-table-column>
      <el-table-column label="" width="92" align="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row)">{{ openLabel(row) }}</el-button>
          <RowMore>
            <el-dropdown-item v-if="canExport(row)" :disabled="!!exporting" @click="exportReport(row)">{{ row.kind === 'bid' ? '导出初审疑点报告' : '导出最新一轮报告' }}</el-dropdown-item>
            <el-dropdown-item class="danger-item" @click="remove(row)">删除</el-dropdown-item>
          </RowMore>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager"><el-pagination
      v-model:current-page="page"
      layout="prev, pager, next"
      :page-size="pageSize"
      :total="total"
      hide-on-single-page
      @current-change="load"
    /></div>
  </div>
</template>

<style scoped>
.head { display: flex; align-items: baseline; gap: 16px; }
.head .entry-title { margin: 0 0 16px; }
.head .hint { margin: 0; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.q { width: 260px; }
.filters :deep(.el-date-editor) { width: 280px; margin-left: auto; }
.pager { display: flex; margin-top: 20px; justify-content: flex-end; }
.history-table :deep(.el-table__row) { height: 56px; }
.file-cell { display: flex; gap: 12px; align-items: center; min-width: 0; }
.file-text { display: flex; flex-direction: column; min-width: 0; }
.file-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--si-ink-1); }
.file-sub { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-caption); color: var(--si-ink-3); }
.file-badge { flex: none; width: 30px; height: 30px; display: grid; place-items: center; box-sizing: border-box; border: 1px solid var(--si-hair); border-radius: 6px; color: var(--si-ink-3); font: var(--si-font-caption); }
.status { display: inline-flex; align-items: center; gap: 6px; color: var(--si-ink-2); }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--si-ink-4); }
.tone-primary .dot, .tone-success .dot { background: var(--si-ink-2); }
.tone-busy .dot { background: transparent; border: 1.5px solid var(--si-brand); box-sizing: border-box; }
.tone-warning .dot { background: var(--si-warn-ink); }
.tone-danger .dot { background: var(--si-danger-ink); }
.expiring { color: var(--si-warn-ink); }
.zero { color: var(--si-ink-5); }
.high-count { color: var(--si-danger-ink); font-weight: 600; }
.pending-count { color: var(--si-warn-ink); font-weight: 600; }
</style>
