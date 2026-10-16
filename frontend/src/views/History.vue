<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { tasksApi, type TaskListItem } from '@/api/tasks'
import { docTypeLabel, stanceLabel, statusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const router = useRouter()

const items = ref<TaskListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const error = ref('')

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
  <div class="page">
    <div class="filters">
      <el-input v-model="q" clearable placeholder="按文件名搜索" class="q" @keyup.enter="search" @clear="search" />
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

    <el-table v-loading="loading" :data="items" border class="gap" empty-text="没有审核记录">
      <el-table-column label="提交时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="title" label="主文件名" min-width="240" show-overflow-tooltip />
      <el-table-column label="文件类型" width="110">
        <template #default="{ row }">{{ docTypeLabel(row.doc_type) }}</template>
      </el-table-column>
      <el-table-column label="立场" width="80">
        <template #default="{ row }">{{ stanceLabel(row.stance) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="130">
        <template #default="{ row }">{{ statusLabel(row.status) }}</template>
      </el-table-column>
      <el-table-column prop="high_count" label="高风险问题数" width="120" align="center" />
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row)">打开</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      class="pager"
      layout="total, prev, pager, next"
      :page-size="pageSize"
      :total="total"
      hide-on-single-page
      @current-change="load"
    />
  </div>
</template>

<style scoped>
.page {
  padding: 16px 0;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.q {
  width: 240px;
}
.gap {
  margin-top: 12px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
