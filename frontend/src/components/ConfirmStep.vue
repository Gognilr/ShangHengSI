<script setup lang="ts">
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import LocalFindings from '@/components/LocalFindings.vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { ApiError } from '@/api/http'
import {
  REDACTION_ERROR_MESSAGES,
  redactionApi,
  type Party,
  type RedactionInfo,
  type Suspect,
  type UserCategory,
} from '@/api/redaction'
import { RUN_ERROR_MESSAGES, runsApi } from '@/api/runs'
import { tasksApi, type TaskDetail } from '@/api/tasks'
import { assistantApi } from '@/api/assistant'
import ManualRedactDialog from './ManualRedactDialog.vue'
import SuspectList from './SuspectList.vue'
import RedactionCompare from './RedactionCompare.vue'
import { DOC_TYPE_OPTIONS, REDACTION_CATEGORY, docTypeSourceLabel, toOptions } from '@/utils/labels'

const props = withDefaults(defineProps<{ task: TaskDetail; mode?: 'review' | 'assistant' }>(), { mode: 'review' })
const assistant = computed(() => props.mode === 'assistant')
const emit = defineEmits<{
  /** 已提交确认发送，父页面应刷新任务状态 */
  (e: 'confirmed'): void
  /** 任务状态已变化（如 409），父页面应刷新任务 */
  (e: 'stale'): void
}>()

const docTypeOptions = toOptions(DOC_TYPE_OPTIONS)
// 类型选项取 SPEC 9.4；国家的建议项是当前识别值和“国家未知”，也可自行输入（Q-50 ②）
const ORG_TYPES = ['贸易商', '货代', '银行', '类型未知']
const UNKNOWN_COUNTRY = '国家未知'

const files = ref(props.task.files)
// Q-95：接口没有独立计数；组件读一次列表给折叠标题计数，默认仍收起。
const localCount = ref<number | null>(null)
const reqDrawer = ref(false)
const skipTemplate = ref(false)
const mainDocType = computed(() => files.value.find((f) => f.is_main)?.doc_type ?? null)
const parties = ref<Party[]>([])
const summary = ref<Record<string, number>>({})
const suspects = ref<Suspect[]>([])
const redactedItems = ref<RedactionInfo['items']>([])
const deciding = ref(false)
const selectedSuspectLoc = ref('')
const compare = ref<InstanceType<typeof RedactionCompare>>()
function locateSuspect(loc: string) {
  selectedSuspectLoc.value = loc
  void compare.value?.locate(loc)
}
const manualOpen = ref(false)
const loading = ref(true)
const loadError = ref('')
const savingParties = ref(false)
const submitting = ref(false)
const submitError = ref('')

// ---- 脱敏摘要 ----
const SUMMARY_UNIT: Record<string, string> = {
  OUR: '处我方名称',
  ORG: '个名称',
  PERSON: '个人名',
  PHONE: '个电话',
  EMAIL: '个邮箱',
  URL: '个网址',
  ADDR: '处地址',
  POSTCODE: '个邮编',
  ACCOUNT: '个账号',
  SWIFT: '个 SWIFT 代码',
  IDNO: '个证件号',
  REFNO: '个编号',
  AMOUNT: '处金额',
  PRICE: '处单价',
  QTY: '处数量',
  CUSTOM: '处敏感词',
}
const summaryText = computed(() => {
  const parts = Object.entries(summary.value)
    .filter(([, n]) => n > 0)
    .map(([code, n]) => `${n} ${SUMMARY_UNIT[code] ?? '处' + (REDACTION_CATEGORY[code] ?? code)}`)
  return parts.length ? `替换了 ${parts.join('、')}` : '没有需要替换的内容'
})

function applyRedaction(info: RedactionInfo) {
  summary.value = info.summary
  suspects.value = info.suspects
  redactedItems.value = info.items
}

async function loadSummary() {
  applyRedaction(await redactionApi.redaction(props.task.id))
}

const pendingCount = computed(() => suspects.value.filter((s) => s.decision === 'pending').length)

/** 替换、忽略、手动补充成功后：以返回值刷新疑似遗漏和摘要，并重新读取签约方（可能出现新的方）和外发文本。 */
async function afterChange(info: RedactionInfo) {
  applyRedaction(info)
  try {
    const [p] = await Promise.all([redactionApi.parties(props.task.id), reloadOutbound()])
    parties.value = p.items
  } catch (e) {
    handleError(e)
  }
}

async function decideSuspect(id: number, decision: 'replace' | 'ignore', category: UserCategory | null, remember = false) {
  if (deciding.value) return
  deciding.value = true
  submitError.value = ''
  try {
    await afterChange(await redactionApi.decideSuspect(props.task.id, id, decision, category, remember))
  } catch (e) {
    handleError(e)
    try {
      await loadSummary()
    } catch {
      // 保持当前显示
    }
  } finally {
    deciding.value = false
  }
}

function onManualDone(info: RedactionInfo, action: 'add' | 'revoke') {
  submitError.value = ''
  ElMessage.success(action === 'revoke' ? '已撤销，脱敏结果已更新' : '已设为敏感信息')
  void afterChange(info)
}

// 上次提交被出口复检拦截时显示拦截原因（只有类别和位置，不含真实值），用户处理后可重新提交
const blockedReason = ref('')

async function loadBlocked() {
  const run = props.task.latest_run
  if (run?.status !== 'blocked') return
  try {
    blockedReason.value = (await runsApi.get(run.id)).error_message ?? '出口复检发现未脱敏的信息，已拦截，未发送'
  } catch {
    blockedReason.value = '出口复检发现未脱敏的信息，已拦截，未发送'
  }
}

async function loadAll() {
  loading.value = true
  loadError.value = ''
  try {
    const [p] = await Promise.all([redactionApi.parties(props.task.id), loadSummary(), loadBlocked(), loadOutbound()])
    parties.value = p.items
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : '加载失败，请刷新页面'
  } finally {
    loading.value = false
  }
}

// ---- 文件类型 ----
async function changeDocType(fileId: string, docType: string) {
  try {
    const updated = await tasksApi.setFileDocType(props.task.id, fileId, docType)
    files.value = files.value.map((f) => (f.id === fileId ? updated : f))
    ElMessage.success('文件类型已修改')
    await reloadOutbound()
  } catch (e) {
    handleError(e)
    // 恢复原值
    files.value = [...files.value]
  }
}

// ---- 签约方 ----
const ourCount = computed(() => parties.value.filter((p) => p.is_our_party).length)
const ourId = computed(() => parties.value.find((p) => p.is_our_party)?.id ?? '')

function countryOptions(p: Party): string[] {
  const set = new Set<string>()
  if (p.country) set.add(p.country)
  set.add(UNKNOWN_COUNTRY)
  return [...set]
}

/** 完整列表提交（后端要求）；成功后用返回值刷新，代号和外发文本随之重算。 */
async function saveParties(next: Party[]) {
  savingParties.value = true
  try {
    const res = await redactionApi.saveParties(
      props.task.id,
      next.map((p) => ({
        id: p.id,
        is_our_party: p.is_our_party,
        country: (p.country ?? '').trim() || UNKNOWN_COUNTRY,
        org_type: (p.org_type ?? '').trim() || '类型未知',
      })),
    )
    parties.value = res.items
    await Promise.all([loadSummary(), reloadOutbound()])
  } catch (e) {
    handleError(e)
    // 以服务端为准恢复显示
    try {
      parties.value = (await redactionApi.parties(props.task.id)).items
    } catch {
      // 保持当前显示
    }
  } finally {
    savingParties.value = false
  }
}

function setOur(id: string) {
  void saveParties(parties.value.map((p) => ({ ...p, is_our_party: p.id === id })))
}

function setField(id: string, field: 'country' | 'org_type', value: string) {
  const v = value.trim()
  if (!v) return
  void saveParties(parties.value.map((p) => (p.id === id ? { ...p, [field]: v } : p)))
}

// ---- 外发文本（纯文本，不用 v-html） ----
const outboundText = ref('')
const outboundNote = ref<string | null>(null)
const outboundLoading = ref(false)
const outboundError = ref('')

async function loadOutbound() {
  outboundLoading.value = true
  outboundError.value = ''
  outboundText.value = ''
  outboundNote.value = null
  try {
    const outbound = await redactionApi.outbound(props.task.id)
    outboundText.value = outbound.text
    outboundNote.value = outbound.note
  } catch (e) {
    outboundError.value = e instanceof ApiError ? e.message : '加载失败'
  } finally {
    outboundLoading.value = false
  }
}

async function reloadOutbound() {
  await loadOutbound()
}

// ---- 确认发送 ----
const canConfirm = computed(
  () => (assistant.value ? ourCount.value <= 1 : ourCount.value === 1) && pendingCount.value === 0 && !savingParties.value && !deciding.value && !loading.value,
)
const disabledReason = computed(() => {
  if (!assistant.value && ourCount.value !== 1) return '请先在签约方中选择我方'
  if (pendingCount.value > 0) return `还有 ${pendingCount.value} 条疑似遗漏未处理`
  return ''
})

function handleError(e: unknown) {
  if (e instanceof ApiError) {
    const base = RUN_ERROR_MESSAGES[e.code] ?? REDACTION_ERROR_MESSAGES[e.code] ?? e.message
    // OUTBOUND_BLOCKED 的 message 带类别和位置，不含真实值，可以一并显示
    submitError.value = e.code === 'OUTBOUND_BLOCKED' && e.message ? `${base}：${e.message}` : base
    if (e.code === 'TASK_STATE_CONFLICT') emit('stale')
  } else {
    submitError.value = '操作失败，请重试'
  }
}

async function confirmSend() {
  if (!canConfirm.value) return
  submitError.value = ''
  submitting.value = true
  try {
    if (assistant.value) await assistantApi.confirm(props.task.id)
    else await runsApi.create(props.task.id, !skipTemplate.value)
    emit('confirmed')
  } catch (e) {
    handleError(e)
  } finally {
    submitting.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div v-if="!assistant && task.template_suggestion" class="panel gap">
    将对照公司范本：{{ task.template_suggestion.name }}
    <el-checkbox v-model="skipTemplate" :disabled="submitting">不对照</el-checkbox>
  </div>
  <div v-loading="loading" class="confirm">
    <el-alert v-if="loadError" type="error" show-icon :closable="false" :title="loadError" />

    <template v-else>
      <el-alert
        v-if="blockedReason"
        type="warning"
        show-icon
        :closable="false"
        title="上次提交被拦截，未发送给模型"
        :description="`${blockedReason}。请检查签约方和外发文本后重新提交。`"
        class="gap"
      />
      <section class="panel">
      <h3><span class="section-number">1</span>
        文件信息
        <el-button v-if="!assistant" link type="primary" @click="reqDrawer = true">查看审核要求</el-button>
      </h3>
      <RequirementsDrawer
        v-model="reqDrawer"
        :stance="task.stance"
        :source="task.source"
        :doc-type="mainDocType"
        :user-note="task.user_note"
      />
      <el-table :data="files" size="small" class="compact-table">
        <el-table-column label="文件名" min-width="200">
          <template #default="{ row }">
            {{ row.original_name }}<el-tag v-if="row.is_main" size="small" class="tag">主文件</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="文件类型" width="190">
          <template #default="{ row }">
            <div class="doc-type-field">
              <el-select
                class="doc-type-select"
                :disabled="assistant"
                :model-value="row.doc_type ?? undefined"
                placeholder="请选择"
                size="small"
                @change="(v: string) => changeDocType(row.id, v)"
              >
                <el-option v-for="o in docTypeOptions" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
              <span class="hint doc-type-source">{{ docTypeSourceLabel(row.doc_type_source) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="规模" width="150">
          <template #default="{ row }">
            <span v-if="row.page_count">{{ row.page_count }} 页，</span>{{ row.paragraph_count }} 段，{{
              row.table_count
            }}
            个表格
          </template>
        </el-table-column>
        <el-table-column label="提示" min-width="220">
          <template #default="{ row }">
            <div v-for="(w, i) in row.warnings" :key="i" class="warning">{{ w }}</div>
            <span v-if="!row.warnings.length" class="hint">—</span>
          </template>
        </el-table-column>
      </el-table>

      </section>
      <section class="panel">
      <h3><span class="section-number">2</span>签约方确认</h3>
      <p class="hint">请选择哪一方是“我方”（已按业务立场预选）；国家和类型可以修改，修改后会重新生成代号和外发文本。</p>
      <p v-if="assistant" class="hint">会话可以不设置我方，最多选择一方。<el-button link :disabled="savingParties" @click="setOur('')">不设我方</el-button></p>
      <el-table :data="parties" size="small" class="compact-table" :class="{ saving: savingParties }" :row-class-name="({ row }: { row: Party }) => row.is_our_party ? 'our-party' : ''">
        <el-table-column label="我方" width="70" align="center">
          <template #default="{ row }">
            <el-radio
              :model-value="ourId"
              :value="row.id"
              :disabled="savingParties"
              @change="setOur(row.id)"
            >
              <span class="sr-only">设为我方</span>
            </el-radio>
          </template>
        </el-table-column>
        <el-table-column label="真实名称" min-width="200"><template #default="{ row }">{{ row.name }}<el-tag v-if="row.is_our_party" size="small" class="tag">我方</el-tag></template></el-table-column>
        <el-table-column label="代号" width="110"><template #default="{ row }"><span class="party-code">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="role_label" label="角色" width="90" />
        <el-table-column label="国家" width="170">
          <template #default="{ row }">
            <el-select
              :model-value="row.country ?? UNKNOWN_COUNTRY"
              filterable
              allow-create
              default-first-option
              size="small"
              :disabled="savingParties"
              @change="(v: string) => setField(row.id, 'country', v)"
            >
              <el-option v-for="c in countryOptions(row)" :key="c" :label="c" :value="c" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="150">
          <template #default="{ row }">
            <el-select
              :model-value="row.org_type ?? '类型未知'"
              filterable
              allow-create
              default-first-option
              size="small"
              :disabled="savingParties"
              @change="(v: string) => setField(row.id, 'org_type', v)"
            >
              <el-option v-for="t in ORG_TYPES" :key="t" :label="t" :value="t" />
            </el-select>
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        v-if="!assistant && !loading && !parties.length"
        type="warning"
        show-icon
        :closable="false"
        title="未识别到签约方，暂时不能提交 AI 审核"
        description="可在“5. 手动补充”中选中我方名称、类别选“公司”，再在签约方列表中设为我方"
        class="gap"
      />
      <el-alert
        v-if="!assistant && parties.length && ourCount !== 1"
        type="warning"
        show-icon
        :closable="false"
        title="请先选择哪一方是我方"
        class="gap"
      />

      </section>
      <section class="panel">
      <h3><span class="section-number">3</span>脱敏摘要</h3>
      <div class="summary-stats">
        <div v-for="([code, count]) in Object.entries(summary).filter(([, n]) => n > 0)" :key="code" class="stat"><div class="stat-label">{{ REDACTION_CATEGORY[code] ?? code }}</div><div class="stat-value">{{ count }}</div></div>
      </div>
      <p class="summary">{{ summaryText }}</p>
      </section>
      <section class="panel">
      <h3>
        <span class="section-number">4</span>疑似遗漏<span v-if="pendingCount" class="pending">还有 {{ pendingCount }} 条未处理</span>
      </h3>
      <SuspectList :suspects="suspects" :files="files" :busy="deciding" :selected-loc="selectedSuspectLoc" @locate="locateSuspect" @decide="decideSuspect" />

      </section>
      <section class="panel manual-panel">
      <div><h3><span class="section-number">5</span>手动补充</h3>
      <p class="hint">识别遗漏的名称等信息，可以在原文中选中后设为敏感信息，相同的文字会在全文一并替换。</p>
      </div>
      <el-button :disabled="loading" @click="manualOpen = true">在原文中选择</el-button>
      <ManualRedactDialog
        v-model="manualOpen"
        :task-id="task.id"
        :files="files"
        :items="redactedItems"
        @done="onManualDone"
        @stale="emit('stale')"
      />
      </section>
      <section class="panel">
      <h3><span class="section-number">6</span>原文与外发文本对照</h3>
      <el-alert v-if="outboundError" type="error" show-icon :closable="false" :title="outboundError" />
      <RedactionCompare ref="compare" :task-id="task.id" :files="files" :outbound-text="outboundText" :note="task.user_note" :outbound-note="outboundNote" :items="redactedItems" :suspects="suspects" :loading="outboundLoading" @done="onManualDone" @busy="deciding = $event" @stale="emit('stale')" />
      </section>
      <section v-if="!assistant" class="panel">
      <h3><span class="section-number">7</span>本地校验结果<span class="hint">{{ localCount ?? '—' }} 条</span></h3>
      <el-collapse>
        <el-collapse-item title="展开本地校验结果" name="local">
          <LocalFindings :task-id="task.id" @count="localCount = $event" />
        </el-collapse-item>
      </el-collapse>
      </section>

      <el-alert v-if="submitError" type="error" show-icon :closable="false" :title="submitError" class="gap" />

      <div class="confirm-actions">
        <ul class="conditions" aria-label="提交条件" aria-live="polite">
          <li v-if="assistant" :class="{ met: ourCount <= 1 }">{{ ourCount ? '已选择一方为我方' : '未设置我方，可以确认脱敏' }}</li>
          <li v-else :class="{ met: ourCount === 1 }">{{ ourCount === 1 ? '✓ 已选择我方' : !parties.length ? '未识别到签约方，暂时不能提交 AI 审核' : '请先选择哪一方是我方' }}</li>
          <li :class="{ met: pendingCount === 0 }">{{ pendingCount === 0 ? '✓ 疑似遗漏已处理' : `疑似遗漏还有 ${pendingCount} 条未处理` }}</li>
          <li v-if="savingParties || deciding">正在保存修改……</li>
          <li v-if="loading">正在读取脱敏信息……</li>
          <li v-if="disabledReason && ourCount === 1 && pendingCount === 0">{{ disabledReason }}</li>
        </ul>
        <el-button
          type="primary"
          :disabled="!canConfirm"
          :loading="submitting"
          @click="confirmSend"
        >
          {{ assistant ? '确认脱敏' : '确认发送并开始审核' }}
        </el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
h3 {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 16px;
  font: var(--si-font-h2);
}
.section-number { display: inline-grid; place-items: center; flex: none; width: 26px; height: 26px; border-radius: 50%; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-size: 14px; }
.compact-table { border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); margin-top: 16px; }
:deep(.our-party) { --el-table-tr-bg-color: var(--el-color-primary-light-9); }
.party-code { background: var(--el-fill-color-light); border-radius: 12px; padding: 4px 8px; font-family: inherit; }
.summary-stats { display: flex; flex-wrap: wrap; gap: 12px; }
.summary-stats .stat { min-width: 100px; }
.summary-stats + .summary { margin-top: 16px; }
.manual-panel { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.manual-panel > .el-button { flex: none; }
/* 滚动容器 .page 有 48px 底部内边距，sticky 会从内边距处起算；用负值抵消，使操作栏贴住视口底边，下方不露出正文。 */
.confirm-actions { position: sticky; bottom: -48px; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 16px 24px; margin-top: 16px; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); background: var(--el-bg-color); box-shadow: var(--el-box-shadow-light); }
.confirm-actions > .el-button { height: 44px; flex: none; }
.conditions { list-style: none; margin: 0; padding: 0; font-size: 13px; color: var(--el-color-warning); }
.conditions .met { color: var(--el-text-color-regular); }
.hint {
  margin: 2px 0;
}
.doc-type-field { display: flex; align-items: center; gap: 8px; }
.doc-type-select { flex: 1; min-width: 0; }
.doc-type-source { flex: none; white-space: nowrap; }
.warning {
  color: var(--el-color-warning);
  font-size: 13px;
}
.pending {
  margin-left: auto;
  background: var(--el-color-warning-light-9);
  border-radius: 12px;
  padding: 4px 12px;
  font-size: 13px;
  font-weight: normal;
  color: var(--el-color-warning);
}
.reason {
  margin-right: 12px;
}
.tag {
  margin-left: 8px;
}
.summary {
  margin: 0;
}
.saving {
  opacity: 0.7;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
