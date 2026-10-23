<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { reportApi } from '@/api/report'
import { tasksApi, type TaskListItem } from '@/api/tasks'
import { docTypeLabel, stanceLabel, statusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import { Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'

const router = useRouter()
function fileBadge(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return ['docx', 'doc', 'wps', 'rtf'].includes(ext) ? 'DOC' : ['xlsx', 'xls', 'et'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'IMG'
}

const items = ref<TaskListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref('')
const exporting = ref('')

async function exportReport(row: TaskListItem) {
  if (exporting.value) return
  exporting.value = row.id
  try {
    if (row.status === 'completed_local') await reportApi.local(row.id)
    else {
      const detail = await tasksApi.get(row.id)
      const latest = detail.latest_run
      if (!latest || ['queued', 'running', 'blocked'].includes(latest.status)) {
        ElMessage.error('最新轮次尚未完成，无法导出，请刷新列表')
        return
      }
      await reportApi.run(latest.id)
    }
    ElMessage.success('报告已下载')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '导出失败，请稍后重试')
  } finally { exporting.value = '' }
}

const q = ref('')
// 日期范围：YYYY-MM-DD（按 Asia/Shanghai 日界，由后端换算）
const range = ref<[string, string] | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await tasksApi.list({
      page: page.value,
      q: q.value.trim() || undefined,
      from: range.value?.[0],
      to: range.value?.[1],
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
  }
}

function search() {
  page.value = 1
  void load()
}

function open(row: TaskListItem) {
  // 未完成的任务由结果页转到新建审核页对应的步骤
  void router.push({ name: 'review-result', params: { taskId: row.id } })
}

async function remove(row: TaskListItem) {
  try {
    await ElMessageBox.confirm(
      `确定彻底删除“${row.title}”吗？原件、脱敏记录和审核结果都会被删除，无法恢复。`,
      '删除审核任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await tasksApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '删除失败，请重试')
  }
}

onMounted(load)
</script>

<template>
  <div class="page page--wide">
    <PageHeader title="审核历史" description="查看和导出本人提交的审核任务" />
    <div class="panel filters">
      <el-input v-model="q" :prefix-icon="Search" aria-label="按文件名搜索" clearable placeholder="按文件名搜索" class="q" @keyup.enter="search" @clear="search" />
      <el-date-picker
        v-model="range"
        type="daterange"
        value-format="YYYY-MM-DD"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        @change="search"
      />
      <el-button type="primary" @click="search">查询</el-button>
    </div>

    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" class="gap" />

    <section class="panel history-table gap">
    <el-table v-loading="loading" :data="items" empty-text="没有审核记录">
      <template #empty><el-empty description="没有审核记录"><el-button @click="router.push({ name: 'review-new' })">去新建审核</el-button></el-empty></template>
      <el-table-column label="提交时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="title" label="主文件名" min-width="240" show-overflow-tooltip><template #default="{ row }"><div class="file-cell"><span class="file-badge" :class="`badge-${fileBadge(row.title).toLowerCase()}`">{{ fileBadge(row.title) }}</span><span class="file-title">{{ row.title }}</span></div></template></el-table-column>
      <el-table-column label="文件类型" width="110">
        <template #default="{ row }">{{ docTypeLabel(row.doc_type) }}</template>
      </el-table-column>
      <el-table-column label="立场" width="80">
        <template #default="{ row }">{{ stanceLabel(row.stance) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="130">
        <template #default="{ row }"><span :class="{ danger: row.status === 'parse_failed' }">{{ statusLabel(row.status) }}</span></template>
      </el-table-column>
      <el-table-column label="高风险问题数" width="120" align="center"><template #default="{ row }"><span :class="{ 'high-count': row.high_count > 0 }">{{ row.high_count }}</span></template></el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row)">打开</el-button>
          <el-button v-if="['reviewed', 'completed_local'].includes(row.status)" link type="primary"
            :loading="exporting === row.id" :disabled="!!exporting && exporting !== row.id" @click="exportReport(row)">导出</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager"><span class="hint">共 {{ total }} 条</span><el-pagination
      v-model:current-page="page"
      layout="prev, pager, next"
      :page-size="pageSize"
      :total="total"
      hide-on-single-page
      @current-change="load"
    /></div>
    </section>
  </div>
</template>

<style scoped>
.filters {
  flex-wrap: wrap;
  margin-bottom: 0;
}
.q {
  width: 320px;
}
.pager {
  display: flex;
  margin-top: 20px;
  align-items: center;
  justify-content: space-between;
}
.history-table { padding: 20px 24px; }
.history-table :deep(.el-table__row) { height: 52px; }
.file-cell { display: flex; gap: 12px; align-items: center; min-width: 0; }
.file-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-badge { flex: none; width: 32px; height: 32px; display: grid; place-items: center; font-size: 12px; font-weight: 600; border-radius: 6px; color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.badge-xls { color: var(--el-color-success); background: var(--el-color-success-light-9); }
.badge-pdf { color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.badge-img { color: var(--el-color-info); background: var(--el-color-info-light-9); }
.danger, .high-count { color: var(--el-color-danger); }
.high-count { font-weight: 600; }
</style>
