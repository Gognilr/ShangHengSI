<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { TEMPLATE_STATE } from '@/utils/labels'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Refresh, Delete } from '@element-plus/icons-vue'
import CoverageCard from '@/components/CoverageCard.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import DocPreview from '@/components/DocPreview.vue'
import FindingCard from '@/components/FindingCard.vue'
import LocalFindings from '@/components/LocalFindings.vue'
import ReRunDialog from '@/components/ReRunDialog.vue'
import { ApiError } from '@/api/http'
import { reportApi } from '@/api/report'
import { runsApi, type Finding, type RunDetail, type RunListItem } from '@/api/runs'
import { tasksApi, type TaskDetail } from '@/api/tasks'
import { formatDate, formatDateTime } from '@/utils/time'
import {
  CATEGORY_OPTIONS,
  HANDLING_OPTIONS,
  SEVERITY_OPTIONS,
  SEVERITY_ORDER,
  VERDICT_TAG,
  RUN_STATUS_TAG,
  docTypeLabel,
  reqSourceLabel,
  runStatusLabel,
  sourceLabel,
  stanceLabel,
  statusLabel,
  toOptions,
  verdictLabel,
  severityLabel,
} from '@/utils/labels'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const task = ref<TaskDetail | null>(null)
const expiryWarnings = computed(() => {
  const current = task.value
  if (!current) return []
  const soon = (value: string | null) => value && Date.parse(value) >= Date.now()
    && Date.parse(value) - Date.now() <= 7 * 86400000
  const messages: string[] = []
  if (soon(current.task_expires_at)) messages.push(`本任务将于 ${formatDate(current.task_expires_at)} 删除，如需留存请导出报告`)
  if (soon(current.original_expires_at)) messages.push(`原件将于 ${formatDate(current.original_expires_at)} 删除，之后不能重新审核和追问`)
  return messages
})
const run = ref<RunDetail | null>(null)
const tab = ref('findings')
const rerunOpen = ref(false)
const rounds = ref<RunListItem[]>([])
const selectedRun = ref('')
const runLoading = ref(false)
const runError = ref('')
const deleting = ref(false)
const exporting = ref(false)
const exportReason = computed(() => task.value?.status === 'completed_local' ? ''
  : runLoading.value ? '正在加载该轮次，请稍候'
    : !run.value ? '请先加载审核轮次'
      : ['queued', 'running', 'blocked'].includes(run.value.status) ? '该轮次尚未完成，无法导出' : '')

async function exportReport() {
  if (!task.value || exporting.value || exportReason.value) return
  exporting.value = true
  try {
    if (task.value.status === 'completed_local') await reportApi.local(task.value.id)
    else if (run.value) await reportApi.run(run.value.id)
    ElMessage.success('报告已下载')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '导出失败，请稍后重试')
  } finally { exporting.value = false }
}
let loadVersion = 0
let runVersion = 0

// 筛选：等级、类别、处理状态
const fSeverity = ref('')
const fCategory = ref('')
const fHandling = ref('')

const severityOptions = toOptions(SEVERITY_OPTIONS)
const categoryOptions = toOptions(CATEGORY_OPTIONS)
const handlingOptions = toOptions(HANDLING_OPTIONS)

async function load() {
  const version = ++loadVersion
  ++runVersion
  const id = String(route.params.taskId)
  loading.value = true
  error.value = ''
  task.value = null
  run.value = null
  rounds.value = []
  selectedRun.value = ''
  selectedId.value = ''
  highlight.value = []
  runError.value = ''
  runLoading.value = false
  try {
    const t = await tasksApi.get(id)
    if (version !== loadVersion) return
    // 还没到结果阶段的任务回到新建审核页，由那里按状态显示对应步骤
    if (!['reviewed', 'completed_local'].includes(t.status)) {
      await router.replace({ name: 'review-new', query: { task: id } })
      return
    }
    task.value = t
    const list = await runsApi.list(id)
    if (version !== loadVersion) return
    rounds.value = list.items
    selectedRun.value = t.latest_run?.id ?? list.items[0]?.id ?? ''
    if (selectedRun.value) await selectRun(selectedRun.value)
  } catch (e) {
    if (version === loadVersion) error.value = loadMessage(e)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

function loadMessage(e: unknown) {
  return e instanceof ApiError
    ? e.status === 404 ? '任务不存在或已被删除' : `${e.message}，请刷新页面后重试`
    : '加载失败，请刷新页面后重试'
}

async function selectRun(id: string) {
  const version = ++runVersion
  runLoading.value = true
  runError.value = ''
  run.value = null
  selectedId.value = ''
  highlight.value = []
  tab.value = 'findings'
  fSeverity.value = ''
  fCategory.value = ''
  fHandling.value = ''
  try {
    const detail = await runsApi.get(id)
    if (version === runVersion) run.value = detail
  } catch (e) {
    if (version === runVersion) runError.value = loadMessage(e)
  } finally {
    if (version === runVersion) runLoading.value = false
  }
}

async function remove() {
  if (!task.value || deleting.value) return
  const current = task.value
  try {
    await ElMessageBox.confirm(
      `确定彻底删除“${current.title}”吗？原件、脱敏记录和全部轮次的审核结果都会被删除，无法恢复。`,
      '删除审核任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  deleting.value = true
  try {
    await tasksApi.remove(current.id)
    ElMessage.success('已删除')
    await router.push({ name: 'history' })
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '删除失败，请重试')
  } finally { deleting.value = false }
}

onMounted(load)
watch(() => route.params.taskId, load)

const mainFile = computed(() => task.value?.files.find((f) => f.is_main) ?? task.value?.files[0])
const severityCounts = computed(() => ['high', 'medium', 'low'].map(value => ({ value, label: severityLabel(value), count: (run.value?.findings ?? []).filter(f => f.severity === value).length })))
const fileBadge = computed(() => {
  const ext = mainFile.value?.ext ?? ''
  return ['docx', 'doc', 'wps', 'rtf'].includes(ext) ? 'DOC' : ['xlsx', 'xls', 'et'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'IMG'
})

const findings = computed<Finding[]>(() => {
  const list = (run.value?.findings ?? []).filter(
    (f) =>
      (!fSeverity.value || f.severity === fSeverity.value) &&
      (!fCategory.value || f.category === fCategory.value) &&
      (!fHandling.value || f.handling_status === fHandling.value),
  )
  return [...list].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) || a.seq - b.seq,
  )
})

// 点击问题：把该问题的 locs 交给右栏预览高亮（每次点击都是新数组，同一问题再点会重新滚动）
const selectedId = ref('')
const highlight = ref<string[]>([])
function onSelect(f: Finding) {
  selectedId.value = f.id
  highlight.value = [...f.locs]
}

function onLocalSelect(locs: string[]) {
  selectedId.value = ''
  highlight.value = [...locs]
}

function onUpdated(updated: Finding) {
  if (!run.value) return
  run.value = { ...run.value, findings: run.value.findings.map((f) => (f.id === updated.id ? updated : f)) }
}

const reqVersion = computed(() => {
  if (!run.value) return '—'
  const parts = [run.value.builtin_req_version ? `内置 ${run.value.builtin_req_version}` : '']
  if (run.value.company_req_version != null) parts.push(`公司补充 v${run.value.company_req_version}`)
  return parts.filter(Boolean).join('，') || '—'
})

</script>

<template>
  <div v-loading="loading" class="page page--split">
    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />

    <div v-else-if="task" class="split">
      <div class="left">
        <el-alert v-for="message in expiryWarnings" :key="message" class="gap" type="warning" show-icon :closable="false" :title="message" />
        <!-- 顶部信息栏 -->
        <section class="panel info">
          <div class="info-heading"><span class="file-badge">{{ fileBadge }}</span><div class="file-heading"><div class="info-title">{{ mainFile?.original_name ?? task.title }}</div><span v-if="task.files.length > 1" class="hint">另有 {{ task.files.length - 1 }} 个附件</span></div>
              <el-tag v-if="run" :type="RUN_STATUS_TAG[run.status] ?? 'info'" size="small">{{ runStatusLabel(run.status) }}</el-tag>
              <template v-else>{{ statusLabel(task.status) }}</template>
          </div>
          <dl class="metadata">
            <div><dt>文件类型</dt><dd>{{ docTypeLabel(run?.main_doc_type ?? mainFile?.doc_type) }}</dd></div>
            <div><dt>立场 / 来源</dt><dd>{{ stanceLabel(run?.stance ?? task.stance) }} / {{ sourceLabel(run?.source ?? task.source) }}</dd></div>
            <div><dt>审核时间</dt><dd>{{ formatDateTime(run?.finished_at) }}</dd></div>
            <div><dt>使用的模型</dt><dd>{{ run?.model_name ?? '—' }}</dd></div>
            <div><dt>审核要求版本</dt><dd>{{ reqVersion }}</dd></div>
            <div><dt>审核轮次</dt><dd><el-select v-if="rounds.length > 1" v-model="selectedRun" aria-label="审核轮次" class="round-select" size="small" @change="selectRun">
            <el-option v-for="item in rounds" :key="item.id" :value="item.id"
              :label="`第 ${item.run_no} 轮 · ${formatDateTime(item.finished_at ?? item.queued_at)} · ${runStatusLabel(item.status)}`" />
          </el-select><span v-else>{{ run ? `第 ${run.run_no} 轮` : '—' }}</span></dd></div>
          </dl>
          <p v-if="run?.template" class="hint">本轮对照公司范本：{{ run.template.name }}{{ TEMPLATE_STATE[run.template.status] ?? '' }}</p>
        </section>

        <el-alert
          v-if="task.status === 'completed_local'"
          type="info"
          show-icon
          :closable="false"
          title="该任务的外发策略为“不外发，仅本地校验”，没有 AI 审核结果。"
          class="gap"
        />

        <section v-if="task.status === 'completed_local'" class="panel gap"><h2 class="local-title">本地校验结果</h2><LocalFindings :task-id="task.id" @select="onLocalSelect" /></section>

        <el-alert v-if="runError" type="error" show-icon :closable="false" :title="runError" class="gap" />
        <div v-loading="runLoading" class="run-content">
        <template v-if="run">
          <!-- Q-80：现有接口不返回 failed_chunks，处理函数仅在有失败分段时设置 partial 的 error_message。 -->
          <el-alert
            v-if="run.status === 'failed'"
            type="error"
            show-icon
            :closable="false"
            :title="run.error_message || '审核失败'"
            class="gap"
          />
          <el-alert
            v-else-if="run.status === 'partial' && run.error_message"
            type="warning"
            show-icon
            :closable="false"
            :title="run.error_message"
            class="gap"
          />

          <div class="severity-counts gap" aria-label="按问题等级筛选"><button v-for="item in severityCounts" :key="item.value" class="stat severity-count" :class="[`severity-${item.value}`, { active: fSeverity === item.value }]" :aria-pressed="fSeverity === item.value" @click="fSeverity = fSeverity === item.value ? '' : item.value"><span class="stat-label"><i />{{ item.label }}</span><strong class="stat-value">{{ item.count }}</strong></button></div>
          <CoverageCard :coverage="run.coverage" :sanctions="run.sanctions" :doc-summary="run.doc_summary" class="gap" />

          <el-tabs v-model="tab" class="gap">
            <el-tab-pane :label="`问题清单（${run.findings.length}）`" name="findings">
              <div class="filters">
                <el-select v-model="fSeverity" clearable placeholder="全部等级" size="small">
                  <el-option v-for="o in severityOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-select v-model="fCategory" clearable placeholder="全部类别" size="small">
                  <el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-select v-model="fHandling" clearable placeholder="全部处理状态" size="small">
                  <el-option v-for="o in handlingOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </div>
              <FindingCard
                v-for="f in findings"
                :key="f.id"
                :finding="f"
                :selected="f.id === selectedId"
                @updated="onUpdated"
                @select="onSelect"
              />
              <el-empty
                v-if="!findings.length"
                :description="run.findings.length ? '没有符合筛选条件的问题' : '本次没有列出问题条目。结果由 AI 生成，仅供参考，请以人工复核为准。'"
              />
            </el-tab-pane>

            <el-tab-pane label="本地校验" name="local" lazy>
              <LocalFindings :task-id="task.id" @select="onLocalSelect" />
            </el-tab-pane>
            <el-tab-pane label="审核要求逐条结论" name="requirements">
              <el-table :data="run.requirement_results" size="small">
                <el-table-column prop="req_id" label="编号" min-width="64" />
                <el-table-column label="来源" min-width="76">
                  <template #default="{ row }">{{ reqSourceLabel(row.req_source) }}</template>
                </el-table-column>
                <el-table-column prop="dimension" label="维度" min-width="60" />
                <el-table-column prop="req_text" label="审核要求" min-width="100" />
                <el-table-column label="结论" min-width="88">
                  <template #default="{ row }">
                    <el-tag :type="VERDICT_TAG[row.verdict] ?? 'info'" size="small">{{ verdictLabel(row.verdict) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="note" label="说明" min-width="100" />
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>
        </div>

        <!-- 底部操作 -->
        <div class="result-actions">
          <ChatPanel :key="task.id" :task-id="task.id" :disabled="deleting" />
          <span v-if="exportReason" class="hint">{{ exportReason }}</span>
          <el-button :icon="Download" :loading="exporting" :disabled="!!exportReason || deleting" @click="exportReport">导出报告</el-button>
          <el-button v-if="task.status === 'reviewed' && task.policy === 'ai' && !task.files.some((f) => f.original_deleted_at)" :icon="Refresh" :disabled="deleting" @click="rerunOpen = true">重新审核</el-button>
          <el-button link type="danger" :icon="Delete" :loading="deleting" @click="remove">删除任务</el-button>
        </div>
        <ReRunDialog v-model="rerunOpen" :task="task" />
      </div>
      <div class="right">
        <DocPreview :task-id="task.id" :files="task.files" :highlight="highlight" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  min-height: 200px;
  /* Q-80：在结果页内滚动，避免 1280 最小宽度叠加窗口纵向滚动条造成横向滚动。 */
  height: calc(100vh - 64px);
  box-sizing: border-box;
  overflow-y: auto;
  /* 顶部 32px 留白放在双栏容器，避免 sticky 再叠加滚动容器的顶部 padding。 */
  padding-top: 0;
}
.split {
  display: flex;
  padding-top: 32px;
  gap: 20px;
  align-items: flex-start;
}
.left {
  flex: 0 0 calc(44% - 10px);
  min-width: 0;
}
.right {
  flex: 0 0 calc(56% - 10px);
  min-width: 0;
  position: sticky;
  top: 24px;
  height: calc(100vh - 64px - 48px);
  box-sizing: border-box;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--si-radius-card);
  background: var(--el-bg-color);
}
.info-title {
  font-size: 18px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.info { padding: 20px 24px; }
.info-heading { display: flex; align-items: center; gap: 12px; }
.file-heading { flex: 1; min-width: 0; }
.file-badge { flex: none; width: 40px; height: 40px; border-radius: 8px; display: grid; place-items: center; color: var(--el-color-primary); background: var(--el-color-primary-light-9); font-size: 12px; font-weight: 600; }
.metadata { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 20px 0 0; }
.metadata > div { min-width: 0; }
dt { color: var(--el-text-color-secondary); font-size: 12px; margin-bottom: 4px; }
dd { margin: 0; font-size: 14px; overflow-wrap: anywhere; }
.severity-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.severity-count { display: flex; align-items: center; justify-content: space-between; gap: 12px; background: var(--el-bg-color); cursor: pointer; font-family: inherit; color: var(--el-text-color-primary); }
.severity-count.active { border-color: var(--el-color-primary); box-shadow: 0 0 0 2px var(--el-color-primary-light-9); }
.severity-count .stat-label { display: flex; align-items: center; gap: 8px; }
.severity-count i { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-info); }
.severity-high i { background: var(--el-color-danger); }
.severity-medium i { background: var(--el-color-warning); }
/* 滚动容器 .page 有 48px 底部内边距，sticky 会从内边距处起算；用负值抵消，使操作栏贴住视口底边，下方不露出问题卡片。 */
.result-actions { position: sticky; bottom: -48px; z-index: 3; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 16px 12px; margin-top: 24px; border-top: 1px solid var(--el-border-color-light); border-radius: 12px; background: color-mix(in srgb, var(--el-bg-color) 96%, transparent); }
.result-actions :deep(.el-button + .el-button) { margin-left: 0; }
.result-actions > .hint { flex-basis: 100%; order: -1; }
.result-actions > .el-button:last-child { margin-left: auto; }
.round-select { width: 100%; }
.local-title { font-size: 18px; margin: 0 0 16px; }
.run-content { min-height: 48px; }
.summary {
  margin: 0 0 8px;
  white-space: pre-wrap;
}
</style>
