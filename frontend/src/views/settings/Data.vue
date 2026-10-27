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
  type RetentionSettings,
  type AuditRow,
  type UserRow,
} from '@/api/admin'
import { ApiError } from '@/api/http'
import { lawsyncApi, type LawsyncSettings } from '@/api/admin'
import { LAWSYNC_INTERVAL } from '@/utils/labels'
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
  AUDIT_ACTION,
  auditActionLabel,
  auditTargetLabel,
} from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import PageHeader from '@/components/PageHeader.vue'

const auth = useAuthStore()
const syncSettings = ref<LawsyncSettings | null>(null)
const syncForm = reactive({ enabled: false, interval: 'monthly' as 'monthly' | 'weekly' })
const syncError = ref('')
const syncLoading = ref(false)
const syncSaving = ref(false)
const syncRunning = ref(false)
let syncTimer: ReturnType<typeof setInterval> | null = null
let syncBaseline: string | null = null
async function loadSync(silent = false) {
  if (syncLoading.value) return
  syncLoading.value = true
  try {
    const result = await lawsyncApi.get()
    syncSettings.value = result
    if (!silent) { Object.assign(syncForm, { enabled: result.enabled, interval: result.interval }); syncError.value = '' }
    if (syncRunning.value && result.last_run_at !== syncBaseline) {
      if (syncTimer) clearInterval(syncTimer)
      syncTimer = null; syncRunning.value = false; await auth.refresh()
    }
  } catch (e) { if (!silent) syncError.value = `加载法规更新设置失败：${e instanceof ApiError ? e.message : '请求未完成'}。请刷新页面后重试。` }
  finally { syncLoading.value = false }
}
async function saveSync() {
  syncSaving.value = true; syncError.value = ''
  try { syncSettings.value = await lawsyncApi.save(syncForm.enabled, syncForm.interval); ElMessage.success('自动更新设置已保存'); await auth.refresh() }
  catch (e) { if (syncSettings.value) Object.assign(syncForm, { enabled: syncSettings.value.enabled, interval: syncSettings.value.interval }); syncError.value = e instanceof ApiError ? e.message : '保存失败，请稍后重试' }
  finally { syncSaving.value = false }
}
async function runSync() {
  syncRunning.value = true; syncError.value = ''; syncBaseline = syncSettings.value?.last_run_at ?? null
  try {
    await lawsyncApi.run(); ElMessage.success('同步已入队，结果需业务管理员确认')
    syncTimer = setInterval(() => { void loadSync(true) }, 3000)
  } catch (e) { syncRunning.value = false; syncError.value = e instanceof ApiError ? e.message : '同步未能开始，请稍后重试' }
}
onMounted(() => { void loadSync() })
onBeforeUnmount(() => { if (syncTimer) clearInterval(syncTimer) })
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
const retention = reactive<RetentionSettings>({ original_days: 30, result_days: 365, outbound_days: 180, audit_days: 0, unfinished_days: 30 })
const retentionLoaded = ref(false)
const retentionOptions = [30, 90, 180, 365, 0]
const retentionFields: { key: keyof RetentionSettings; label: string }[] = [
  { key: 'original_days', label: '原件与原文' }, { key: 'result_days', label: '审核结果与对话' },
  { key: 'outbound_days', label: '外发记录正文' }, { key: 'audit_days', label: '审计日志' },
  { key: 'unfinished_days', label: '未完成任务' },
]
const estimateReasons = { insufficient_history: '备份统计不足 7 天，暂无法估算', no_growth: '近 30 天没有新增数据', unavailable: '无法读取数据盘剩余空间' }
const retentionSaving = ref(false)
const retentionError = ref('')

async function loadRetention() {
  retentionError.value = ''
  try {
    Object.assign(retention, await adminDataApi.getRetention())
    retentionLoaded.value = true
  } catch (e) {
    retentionError.value = `加载失败：${errText(e, '请刷新页面')}`
  }
}

async function saveRetention() {
  retentionSaving.value = true
  try {
    Object.assign(retention, await adminDataApi.saveRetention({ ...retention }))
    ElMessage.success('已保存')
    await auth.refresh()
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
const statusLoading = ref(false)
const statusError = ref('')

async function loadStatus() {
  statusLoading.value = true
  statusError.value = ''
  try {
    status.value = await adminDataApi.systemStatus()
  } catch (e) {
    statusError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    statusLoading.value = false
  }
}

// ---- ⑤ 只读审计日志 ----
const auditFilters = reactive<{ user_id: string; action: string; range: [string, string] | null }>({ user_id: '', action: '', range: null })
const auditRows = ref<AuditRow[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditLoading = ref(false)
const auditError = ref('')

async function loadAudit() {
  auditLoading.value = true
  auditError.value = ''
  try {
    const result = await adminDataApi.auditLogs({ user_id: auditFilters.user_id || undefined, action: auditFilters.action || undefined,
      from: auditFilters.range?.[0], to: auditFilters.range?.[1], page: auditPage.value })
    auditRows.value = result.items
    auditTotal.value = result.total
  } catch (e) {
    auditError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally { auditLoading.value = false }
}

function searchAudit() {
  auditPage.value = 1
  void loadAudit()
}

onMounted(async () => {
  void loadBackup()
  void loadRetention()
  void loadLogs()
  void loadStatus()
  void loadAudit()
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
    <PageHeader title="数据与系统" description="系统状态、数据保留、备份与外发记录" />
    <section v-loading="statusLoading" class="panel">
      <h3 class="first">系统状态</h3>
      <el-alert v-if="statusError" type="error" :closable="false" show-icon :title="statusError" />
      <div v-if="status" class="system-stats">
        <div class="stat"><div class="stat-label">版本号</div><div class="stat-value">{{ status.version }}</div></div>
        <div class="stat"><div class="stat-label">排队任务数</div><div class="stat-value">{{ status.queued_jobs }}</div></div>
        <div class="stat"><div class="stat-label">数据盘剩余空间</div><div class="stat-value">{{ formatBytes(status.data_free_bytes) }}</div></div>
        <div class="stat"><div class="stat-label">原件占用</div><div class="stat-value">{{ formatBytes(status.files_bytes) }}</div></div>
        <div class="stat"><div class="stat-label">数据库占用</div><div class="stat-value">{{ formatBytes(status.db_bytes) }}</div></div>
        <div class="stat"><div class="stat-label">预计可用天数</div><div class="stat-value" :class="{ warning: status.estimated_days !== null && status.estimated_days < 30 }">{{ status.estimated_days ?? '—' }}</div></div>
        <div class="stat"><div class="stat-label">备份目录占用</div><div class="stat-value">{{ formatBytes(status.backup_dir_bytes) }}</div></div>
        <div class="stat"><div class="stat-label">最近一次备份大小</div><div class="stat-value">{{ formatBytes(status.latest_backup_bytes) }}</div></div>
        <div class="stat"><div class="stat-label">备份盘剩余空间</div><div class="stat-value">{{ formatBytes(status.backup_free_bytes) }}</div></div>
      </div>
      <template v-if="status">
        <p class="hint">存储用量统计时间：{{ formatDateTime(status.storage_measured_at) }}</p>
        <p v-if="status.backup_stats_at" class="hint">备份三项（{{ formatDateTime(status.backup_stats_at) }} 备份时统计）</p>
        <p v-if="status.estimate_reason" class="hint">{{ estimateReasons[status.estimate_reason] }}</p>
      </template>
    </section>

<section v-loading="syncLoading" class="panel">
      <h3 class="first">法规库自动更新</h3>
      <el-alert v-if="syncError" :title="syncError" type="error" :closable="false" show-icon />
      <el-form label-width="110px">
        <el-form-item label="自动更新"><el-switch v-model="syncForm.enabled" :disabled="!syncSettings || syncSaving || syncRunning" aria-label="法规库自动更新" /></el-form-item>
        <el-form-item label="更新频率"><el-select v-model="syncForm.interval" :disabled="!syncSettings || syncSaving || syncRunning" aria-label="法规更新频率"><el-option v-for="(label, value) in LAWSYNC_INTERVAL" :key="value" :value="value" :label="label" /></el-select></el-form-item>
        <el-form-item label="上次运行">{{ formatDateTime(syncSettings?.last_run_at) }}</el-form-item>
        <el-form-item label="运行结果">{{ syncRunning ? '已入队，等待同步结果' : !syncSettings?.last_status ? '尚未运行' : syncSettings.last_status.ok ? '已生成待确认更新' : '同步失败，可改用手动导入' }}</el-form-item>
      </el-form>
      <p class="hint">默认关闭。只从官方数据库拉取公开法规，目录与全文变更经业务管理员确认后才生效。</p>
      <p v-if="!syncSettings?.enabled" class="hint">请先开启并保存设置，再立即同步。</p>
      <p v-else-if="syncForm.enabled !== syncSettings.enabled || syncForm.interval !== syncSettings.interval" class="hint">设置已修改，请先保存后同步。</p>
      <div class="actions"><el-button :disabled="!syncSettings?.enabled || syncSaving || syncForm.enabled !== syncSettings.enabled || syncForm.interval !== syncSettings.interval" :loading="syncRunning" @click="runSync">立即同步</el-button><el-button type="primary" :disabled="!syncSettings || syncRunning" :loading="syncSaving" @click="saveSync">保存自动更新设置</el-button></div>
    </section>

<section class="panel">
      <h3 class="first">数据保留</h3>
      <el-alert v-if="retentionError" type="error" :closable="false" show-icon :title="retentionError" />
      <el-form label-width="110px" @submit.prevent="saveRetention">
        <el-form-item v-for="field in retentionFields" :key="field.key" :label="field.label">
          <el-select v-model="retention[field.key]" :disabled="!retentionLoaded || retentionSaving" class="retention-select" :aria-label="field.label">
            <el-option v-if="!retentionOptions.includes(retention[field.key])" :value="retention[field.key]" :label="`${retention[field.key]} 天（旧版设置）`" />
            <el-option v-for="days in retentionOptions" :key="days" :value="days" :label="days === 0 ? '永久' : `${days} 天`" />
          </el-select>
          <span v-if="!retentionOptions.includes(retention[field.key])" class="hint inline">请改选一个档位后保存</span>
        </el-form-item>
      </el-form>
      <div class="hint">
        原件到期后不能重新审核和追问；审核结果到期后整个任务删除。未完成任务按无活动时间清理。旧备份中的数据会保留至备份被轮换删除。
      </div>
      <div class="actions">
        <el-button type="primary" :loading="retentionSaving" :disabled="!retentionLoaded" @click="saveRetention">保存</el-button>
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
            <span>{{ records?.free_bytes == null ? '—（尚无成功备份的统计）' : formatBytes(records.free_bytes) }}</span>
            <span v-if="records?.free_measured_at" class="hint inline">（{{ formatDateTime(records.free_measured_at) }} 备份时统计）</span>
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
          <el-table-column label="开始时间" width="170" class-name="num">
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
          <el-table-column label="大小" width="100" class-name="num">
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
        <el-table-column label="时间" width="170" class-name="num">
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
        <el-table-column prop="chars" label="字数" width="80" class-name="num" />
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

    <section class="panel">
      <h3 class="first">审计日志</h3>
      <div class="filters">
        <el-select v-model="auditFilters.user_id" clearable filterable placeholder="全部用户" class="user-filter" @change="searchAudit">
          <el-option v-for="u in users" :key="u.id" :label="u.display_name" :value="u.id" />
        </el-select>
        <el-select v-model="auditFilters.action" clearable placeholder="全部动作" class="status-filter" @change="searchAudit">
          <el-option v-for="option in toOptions(AUDIT_ACTION)" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-date-picker v-model="auditFilters.range" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" class="date-filter" @change="searchAudit" />
      </div>
      <el-alert v-if="auditError" type="error" :closable="false" show-icon :title="auditError" />
      <el-table v-loading="auditLoading" :data="auditRows" size="small" empty-text="没有符合条件的审计日志">
        <el-table-column label="时间" width="170" class-name="num"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="用户" width="100"><template #default="{ row }">{{ row.user_name ?? '—' }}</template></el-table-column>
        <el-table-column label="动作" width="120"><template #default="{ row }">{{ auditActionLabel(row.action) }}</template></el-table-column>
        <el-table-column label="对象" min-width="150"><template #default="{ row }">{{ row.target_type ? auditTargetLabel(row.target_type) : '—' }} / {{ row.target_id ?? '—' }}</template></el-table-column>
        <el-table-column label="详情" min-width="180"><template #default="{ row }"><span class="audit-detail">{{ JSON.stringify(row.detail) }}</span></template></el-table-column>
        <el-table-column label="IP" min-width="110"><template #default="{ row }">{{ row.ip ?? '—' }}</template></el-table-column>
      </el-table>
      <el-pagination v-if="auditTotal > 20" v-model:current-page="auditPage" class="pager" :page-size="20" :total="auditTotal" layout="total, prev, pager, next" @current-change="loadAudit" />
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
          <pre class="plain">{{ detail.request_text ?? detail.body_notice ?? '（未发送）' }}</pre>
          <h3>返回内容</h3>
          <pre class="plain">{{ detail.response_text ?? detail.body_notice ?? '（无）' }}</pre>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
:deep(.el-dialog__title), :deep(.el-drawer__title) { font: var(--si-font-h2); }
:deep(.el-dialog__body), :deep(.el-drawer__body) { font: var(--si-font-body); }

h3 {
  margin: 20px 0 16px;
  font: var(--si-font-h2);
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
.warning { color: var(--el-color-warning); }
.audit-detail { white-space: pre-wrap; overflow-wrap: anywhere; }
.retention-select { width: 220px; }
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
