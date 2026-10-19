<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocPreview from '@/components/DocPreview.vue'
import FindingCard from '@/components/FindingCard.vue'
import ReRunDialog from '@/components/ReRunDialog.vue'
import { ApiError } from '@/api/http'
import { runsApi, type Finding, type RunDetail } from '@/api/runs'
import { tasksApi, type TaskDetail } from '@/api/tasks'
import { formatDateTime } from '@/utils/time'
import {
  CATEGORY_OPTIONS,
  HANDLING_OPTIONS,
  SEVERITY_OPTIONS,
  SEVERITY_ORDER,
  VERDICT_TAG,
  docTypeLabel,
  reqSourceLabel,
  runStatusLabel,
  sanctionsLabel,
  sourceLabel,
  stanceLabel,
  statusLabel,
  toOptions,
  verdictLabel,
} from '@/utils/labels'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const task = ref<TaskDetail | null>(null)
const run = ref<RunDetail | null>(null)
const tab = ref('findings')
const rerunOpen = ref(false)

// 筛选：等级、类别、处理状态
const fSeverity = ref('')
const fCategory = ref('')
const fHandling = ref('')

const severityOptions = toOptions(SEVERITY_OPTIONS)
const categoryOptions = toOptions(CATEGORY_OPTIONS)
const handlingOptions = toOptions(HANDLING_OPTIONS)

async function load() {
  const id = String(route.params.taskId)
  loading.value = true
  error.value = ''
  task.value = null
  run.value = null
  try {
    const t = await tasksApi.get(id)
    // 还没到结果阶段的任务回到新建审核页，由那里按状态显示对应步骤
    if (!['reviewed', 'completed_local'].includes(t.status)) {
      await router.replace({ name: 'review-new', query: { task: id } })
      return
    }
    task.value = t
    if (t.latest_run) run.value = await runsApi.get(t.latest_run.id)
  } catch (e) {
    error.value = e instanceof ApiError && e.status === 404 ? '任务不存在或已被删除' : '加载失败，请刷新页面'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => route.params.taskId, load)

const mainFile = computed(() => task.value?.files.find((f) => f.is_main) ?? task.value?.files[0])

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

function onUpdated(updated: Finding) {
  if (!run.value) return
  run.value = { ...run.value, findings: run.value.findings.map((f) => (f.id === updated.id ? updated : f)) }
}

const reqVersion = computed(() => {
  if (!run.value) return '—'
  const parts = [run.value.builtin_req_version ? `内置 ${run.value.builtin_req_version}` : '']
  if (run.value.company_req_version) parts.push(`公司补充 v${run.value.company_req_version}`)
  return parts.filter(Boolean).join('，') || '—'
})

</script>

<template>
  <div v-loading="loading" class="page page--split">
    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />

    <div v-else-if="task" class="split">
      <div class="left">
        <!-- 顶部信息栏 -->
        <el-card shadow="never" class="info">
          <div class="info-title">{{ mainFile?.original_name ?? task.title }}</div>
          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item label="文件类型">{{ docTypeLabel(run?.main_doc_type ?? mainFile?.doc_type) }}</el-descriptions-item>
            <el-descriptions-item label="立场">{{ stanceLabel(run?.stance ?? task.stance) }}</el-descriptions-item>
            <el-descriptions-item label="来源">{{ sourceLabel(run?.source ?? task.source) }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              {{ run ? runStatusLabel(run.status) : statusLabel(task.status) }}
            </el-descriptions-item>
            <el-descriptions-item label="审核时间">{{ formatDateTime(run?.finished_at ?? task.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="使用的模型">{{ run?.model_name ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="审核要求版本" :span="2">{{ reqVersion }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-alert
          v-if="task.status === 'completed_local'"
          type="info"
          show-icon
          :closable="false"
          title="该任务的外发策略为“不外发，仅本地校验”，没有 AI 审核结果。"
          class="gap"
        />

        <template v-if="run">
          <el-alert
            v-if="run.status === 'failed'"
            type="error"
            show-icon
            :closable="false"
            :title="run.error_message || '审核失败'"
            class="gap"
          />
          <el-alert
            v-else-if="run.status === 'partial'"
            type="warning"
            show-icon
            :closable="false"
            title="部分审核要求无法判断，结果仅供参考。"
            class="gap"
          />

          <el-card v-if="run.doc_summary || run.sanctions.level" shadow="never" class="gap">
            <p v-if="run.doc_summary" class="summary">{{ run.doc_summary }}</p>
            <div v-if="run.sanctions.level" class="sanctions">
              <b>制裁结论：</b>{{ sanctionsLabel(run.sanctions.level) }}
              <ul v-if="run.sanctions.reasons.length">
                <li v-for="(r, i) in run.sanctions.reasons" :key="i">{{ r }}</li>
              </ul>
              <div class="note">未联网核查，基于模型自身知识，需合规人员确认最新制裁与出口管制规定。</div>
            </div>
          </el-card>

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

            <el-tab-pane label="审核要求逐条结论" name="requirements">
              <el-table :data="run.requirement_results" border size="small">
                <el-table-column prop="req_id" label="编号" width="90" />
                <el-table-column label="来源" width="120">
                  <template #default="{ row }">{{ reqSourceLabel(row.req_source) }}</template>
                </el-table-column>
                <el-table-column prop="dimension" label="维度" width="100" />
                <el-table-column prop="req_text" label="审核要求" min-width="260" />
                <el-table-column label="结论" width="110">
                  <template #default="{ row }">
                    <el-tag :type="VERDICT_TAG[row.verdict] ?? 'info'" size="small">{{ verdictLabel(row.verdict) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="note" label="说明" min-width="220" />
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </template>

        <!-- 底部操作 -->
        <div v-if="task.status === 'reviewed' && task.policy === 'ai'" class="actions">
          <el-button @click="rerunOpen = true">重新审核</el-button>
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
}
.split {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.left {
  flex: 0 0 calc(44% - 8px);
  min-width: 0;
}
.right {
  flex: 0 0 calc(56% - 8px);
  min-width: 0;
  position: sticky;
  top: 16px;
  height: calc(100vh - 56px - 32px);
  box-sizing: border-box;
  padding: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--el-border-radius-base);
  background: var(--el-bg-color);
}
.info-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 10px;
}
.summary {
  margin: 0 0 8px;
  white-space: pre-wrap;
}
.sanctions ul {
  margin: 4px 0;
  padding-left: 20px;
}
.note {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
