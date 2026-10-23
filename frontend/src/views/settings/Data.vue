<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  adminDataApi,
  adminUsersApi,
  type BackupRecords,
  type OutboundDetail,
  type OutboundRow,
  type SystemStatus,
  type UserRow,
} from '@/api/admin'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import {
  BACKUP_STATUS_TAG,
  OUTBOUND_STATUS,
  OUTBOUND_STATUS_TAG,
  backupStatusLabel,
  backupTriggerLabel,
  llmPurposeLabel,
  outboundStatusLabel,
  toOptions,
} from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import PageHeader from '@/components/PageHeader.vue'

const auth = useAuthStore()
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback)

/** 字节数显示为 MB / GB，保留 1 位小数。 */
function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const gb = n / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(n / 1024 ** 2).toFixed(1)} MB`
}

// ---- ① 备份 ----
const backup = reactive({ time: '02:00', keep: 7 })
const records = ref<BackupRecords | null>(null)
const backupLoading = ref(false)
const backupError = ref('')
const backupSaving = ref(false)
const backupStarting = ref(false)

async function loadBackup() {
  backupLoading.value = true
  backupError.value = ''
  try {
    const [s, r] = await Promise.all([adminDataApi.getBackupSettings(), adminDataApi.backupRecords()])
    backup.time = s.time
    backup.keep = s.keep
    records.value = r
  } catch (e) {
    backupError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    backupLoading.value = false
  }
}

async function saveBackup() {
  backupSaving.value = true
  try {
    const s = await adminDataApi.saveBackupSettings(backup.time, backup.keep)
    backup.time = s.time
    backup.keep = s.keep
    ElMessage.success('已保存')
    await auth.refresh()
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  } finally {
    backupSaving.value = false
  }
}

// 点击“立即备份”后每 3 秒刷新记录，直到出现新的、已结束的记录；离开页面停止（SPEC 21）
let timer: ReturnType<typeof setInterval> | null = null
let baselineId = 0
let polls = 0
const MAX_POLLS = 200
const polling = ref(false)

function stopPolling() {
  if (timer !== null) clearInterval(timer)
  timer = null
  polling.value = false
}

async function pollOnce() {
  try {
    records.value = await adminDataApi.backupRecords()
  } catch {
    return // 网络错误不打断轮询
  }
  polls += 1
  const latest = records.value.latest
  const finished = latest !== null && latest.id > baselineId && latest.status !== 'running'
  if (finished || polls >= MAX_POLLS) {
    stopPolling()
    await auth.refresh()
  }
}

async function runBackup() {
  backupStarting.value = true
  try {
    baselineId = records.value?.latest?.id ?? 0
    await adminDataApi.runBackup()
    ElMessage.success('已开始备份')
    polls = 0
    polling.value = true
    if (timer === null) timer = setInterval(() => void pollOnce(), 3000)
    await pollOnce()
  } catch (e) {
    ElMessage.error(errText(e, '无法开始备份，请稍后重试'))
  } finally {
    backupStarting.value = false
  }
}

// ---- ② 原件保留期限 ----
const retentionDays = ref(0)
const retentionSaving = ref(false)
const retentionError = ref('')

async function loadRetention() {
  retentionError.value = ''
  try {
    retentionDays.value = (await adminDataApi.getRetention()).original_days
  } catch (e) {
    retentionError.value = `加载失败：${errText(e, '请刷新页面')}`
  }
}

async function saveRetention() {
  retentionSaving.value = true
  try {
    retentionDays.value = (await adminDataApi.saveRetention(retentionDays.value)).original_days
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  } finally {
    retentionSaving.value = false
  }
}

// ---- ③ 外发记录 ----
const users = ref<UserRow[]>([])
const filters = reactive<{ user_id: string; range: [string, string] | null; status: string }>({
  user_id: '',
  range: null,
  status: '',
})
const logs = ref<OutboundRow[]>([])
const logTotal = ref(0)
const logPage = ref(1)
const logSize = ref(20)
const logLoading = ref(false)
const logError = ref('')
const drawer = ref(false)
const detail = ref<OutboundDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

async function loadLogs() {
  logLoading.value = true
  logError.value = ''
  try {
    const res = await adminDataApi.outboundLogs({
      user_id: filters.user_id || undefined,
      from: filters.range?.[0],
      to: filters.range?.[1],
      status: filters.status || undefined,
      page: logPage.value,
    })
    logs.value = res.items
    logTotal.value = res.total
    logSize.value = res.page_size
  } catch (e) {
    logError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    logLoading.value = false
  }
}

function search() {
  logPage.value = 1
  void loadLogs()
}

async function openDetail(row: OutboundRow) {
  drawer.value = true
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await adminDataApi.outboundLog(row.id)
  } catch (e) {
    detailError.value = `加载失败：${errText(e, '请稍后重试')}`
  } finally {
    detailLoading.value = false
  }
}

// ---- ④ 系统状态 ----
const status = ref<SystemStatus | null>(null)
const statusError = ref('')

async function loadStatus() {
  statusError.value = ''
  try {
    status.value = await adminDataApi.systemStatus()
  } catch (e) {
    statusError.value = `加载失败：${errText(e, '请刷新页面')}`
  }
}

onMounted(async () => {
  void loadBackup()
  void loadRetention()
  void loadLogs()
  void loadStatus()
  try {
    users.value = (await adminUsersApi.list()).items
  } catch {
    users.value = [] // 用户下拉取不到时仍可按其他条件筛选
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="settings-page">
    <PageHeader title="数据与系统" description="系统状态、原件保留期限、备份与外发记录" />
    <section class="panel">
      <h3 class="first">系统状态</h3>
      <el-alert v-if="statusError" type="error" :closable="false" show-icon :title="statusError" />
      <div v-if="status" class="system-stats">
        <div class="stat"><div class="stat-label">版本号</div><div class="stat-value">{{ status.version }}</div></div>
        <div class="stat"><div class="stat-label">排队任务数</div><div class="stat-value">{{ status.queued_jobs }}</div></div>
        <div class="stat"><div class="stat-label">数据盘剩余空间</div><div class="stat-value">{{ formatBytes(status.data_free_bytes) }}</div></div>
      </div>
    </section>

<section class="panel">
      <h3 class="first">原件保留期限</h3>
      <el-alert v-if="retentionError" type="error" :closable="false" show-icon :title="retentionError" />
      <el-form label-width="110px" @submit.prevent="saveRetention">
        <el-form-item label="保留天数">
          <el-input-number v-model="retentionDays" :min="0" :max="3650" />
          <span class="hint inline">0 表示永久保留</span>
        </el-form-item>
      </el-form>
      <div class="hint">
        到期后删除原件和节点原文，保留审核结果和报告；删除后不能重新审核和追问。旧备份中仍保存着这些原件，直到被轮换删除。
      </div>
      <div class="actions">
        <el-button type="primary" :loading="retentionSaving" @click="saveRetention">保存</el-button>
      </div>
    </section>

<section class="panel">
      <h3 class="first">备份</h3>
      <el-alert v-if="backupError" type="error" :closable="false" show-icon :title="backupError" />
      <div v-loading="backupLoading">
        <el-form label-width="110px" @submit.prevent="saveBackup">
          <el-form-item label="备份时间">
            <el-time-picker v-model="backup.time" format="HH:mm" value-format="HH:mm" :clearable="false" />
          </el-form-item>
          <el-form-item label="保留份数">
            <el-input-number v-model="backup.keep" :min="1" :max="60" />
            <span class="hint inline">1–60 份，超出的最旧备份会被自动删除</span>
          </el-form-item>
          <el-form-item label="备份目录">
            <span>{{ records?.dir ?? '—' }}</span>
          </el-form-item>
          <el-form-item label="剩余空间">
            <span>{{ records?.free_bytes === null ? '备份目录不可用' : formatBytes(records?.free_bytes) }}</span>
          </el-form-item>
          <el-form-item label="最近一次">
            <template v-if="records?.latest">
              <el-tag size="small" :type="BACKUP_STATUS_TAG[records.latest.status]">
                {{ backupStatusLabel(records.latest.status) }}
              </el-tag>
              <span class="inline">{{ formatDateTime(records.latest.finished_at ?? records.latest.started_at) }}</span>
              <span v-if="records.latest.file_name" class="inline">{{ records.latest.file_name }}（{{ formatBytes(records.latest.size_bytes) }}）</span>
            </template>
            <span v-else class="hint">尚无备份记录</span>
          </el-form-item>
        </el-form>
        <div class="actions">
          <el-button :loading="backupStarting || polling" @click="runBackup">{{ polling ? '备份进行中……' : '立即备份' }}</el-button>
          <el-button type="primary" :loading="backupSaving" @click="saveBackup">保存</el-button>
        </div>

        <h3>最近 20 条备份记录</h3>
        <el-table :data="records?.items ?? []" size="small" empty-text="尚无备份记录">
          <el-table-column label="开始时间" width="170">
            <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
          </el-table-column>
          <el-table-column label="方式" width="70">
            <template #default="{ row }">{{ backupTriggerLabel(row.trigger) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="BACKUP_STATUS_TAG[row.status]">{{ backupStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="文件名" min-width="200">
            <template #default="{ row }">{{ row.file_name ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ row.size_bytes === null ? '—' : formatBytes(row.size_bytes) }}</template>
          </el-table-column>
          <el-table-column label="说明" min-width="200">
            <template #default="{ row }">
              <span v-if="row.error_message" class="danger">{{ row.error_message }}</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>

<section class="panel">
      <h3 class="first">外发记录</h3>
      <div class="filters">
        <el-select v-model="filters.user_id" clearable filterable placeholder="全部用户" class="user-filter" @change="search">
          <el-option v-for="u in users" :key="u.id" :label="u.display_name" :value="u.id" />
        </el-select>
        <el-date-picker
          v-model="filters.range"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          class="date-filter"
          @change="search"
        />
        <el-select v-model="filters.status" clearable placeholder="全部状态" class="status-filter" @change="search">
          <el-option v-for="o in toOptions(OUTBOUND_STATUS)" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
      </div>
      <el-alert v-if="logError" type="error" :closable="false" show-icon :title="logError" />
      <el-table v-loading="logLoading" :data="logs" size="small" empty-text="没有符合条件的外发记录" @row-click="openDetail">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="用户" width="110">
          <template #default="{ row }">{{ row.user_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="任务主文件名" min-width="160">
          <template #default="{ row }">{{ row.task_title ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="用途" width="90">
          <template #default="{ row }">{{ llmPurposeLabel(row.purpose) }}</template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型" width="130" />
        <el-table-column prop="chars" label="字数" width="80" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="OUTBOUND_STATUS_TAG[row.status]">{{ outboundStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="logTotal > logSize"
        v-model:current-page="logPage"
        class="pager"
        :page-size="logSize"
        :total="logTotal"
        layout="total, prev, pager, next"
        @current-change="loadLogs"
      />
      <div class="hint">点击一行查看外发文本和返回内容（均为脱敏后的内容）。</div>
    </section>

    <el-drawer v-model="drawer" title="外发记录详情" direction="rtl" size="560px">
      <el-alert v-if="detailError" type="error" :closable="false" show-icon :title="detailError" />
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions border size="small" :column="1">
            <el-descriptions-item label="时间">{{ formatDateTime(detail.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="用户">{{ detail.user_name ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="任务主文件名">{{ detail.task_title ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="用途 / 模型">{{ llmPurposeLabel(detail.purpose) }} / {{ detail.model_name }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ outboundStatusLabel(detail.status) }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="detail.block_reason" class="gap" type="warning" :closable="false" show-icon title="拦截原因（类别和位置）" :description="detail.block_reason" />
          <el-alert v-if="detail.error_message" class="gap" type="error" :closable="false" show-icon title="错误信息" :description="detail.error_message" />
          <h3>外发文本</h3>
          <pre class="plain">{{ detail.request_text ?? '（未发送）' }}</pre>
          <h3>返回内容</h3>
          <pre class="plain">{{ detail.response_text ?? '（无）' }}</pre>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
h3 {
  margin: 20px 0 16px;
  font-size: 18px;
  font-weight: 600;
}
h3.first {
  margin-top: 0;
}
.inline {
  margin-left: 12px;
}
.danger {
  color: var(--el-color-danger);
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.plain {
  margin: 0;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
  font-size: 13px;
  max-height: 320px;
  overflow: auto;
}
:deep(.el-table__row) {
  cursor: pointer;
}
.system-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.system-stats .stat-value { overflow-wrap: anywhere; }
.user-filter { width: 160px; }
.date-filter { width: 260px; }
.status-filter { width: 140px; }
.filters { flex-wrap: wrap; }
.actions { padding-top: 20px; border-top: 1px solid var(--el-border-color-lighter); }
</style>
