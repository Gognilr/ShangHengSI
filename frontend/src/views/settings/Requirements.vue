<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  adminRequirementsApi,
  type CompanyReqBody,
  type CompanyReqRow,
  type RequirementVersionRow,
} from '@/api/admin'
import { ApiError } from '@/api/http'
import { requirementsApi, type BuiltinRequirement } from '@/api/requirements'
import { DOC_TYPE_OPTIONS, STANCE_OPTIONS, SOURCE_OPTIONS, docTypeLabel, stanceLabel, sourceLabel, toOptions } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import EmptyState from '@/components/EmptyState.vue'
import RowMore from '@/components/RowMore.vue'
import SiBadge from '@/components/ui/SiBadge.vue'
import { ChevronDown, ChevronUp, FileText, GripVertical, Plus } from '@lucide/vue'
import RatioSetting from '@/components/settings/RatioSetting.vue'
import SharedContent from '@/components/settings/SharedContent.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import SettingSection from '@/components/SettingSection.vue'
import SaveState from '@/components/SaveState.vue'
import { useAutoSave } from '@/utils/autosave'

const MAX_ITEMS = 100
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback)

// ---- ① 内置要求（只读） ----
const builtin = ref<BuiltinRequirement[]>([])
const builtinVersion = ref('')
const builtinType = ref('')
const builtinLoading = ref(false)
const builtinError = ref('')

async function loadBuiltin() {
  builtinLoading.value = true
  builtinError.value = ''
  try {
    const res = await requirementsApi.get({ docType: builtinType.value || null })
    builtin.value = res.builtin
    builtinVersion.value = res.builtin_version
  } catch (e) {
    builtinError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    builtinLoading.value = false
  }
}

const scopeText = (values: string[], label: (v: string) => string) =>
  values.includes('all') ? '全部' : values.map(label).join('、')

// ---- ② 公司补充要求 ----
const companyFilter = ref('all')
const companyFilters = [{ value: 'all', label: '文件审核要求' }, { value: 'shared', label: '标书沉淀' }]
const items = ref<CompanyReqRow[]>([])
const comboCounts = ref<{ doc_type: string; stance: string; source: string; count: number }[]>([])
const comboColumns = toOptions(STANCE_OPTIONS).flatMap(stance => toOptions(SOURCE_OPTIONS).map(source => ({ stance: stance.value, source: source.value, label: stance.label + ' / ' + source.label })))
const comboRows = computed(() => toOptions(DOC_TYPE_OPTIONS).map(doc => ({ doc_type: doc.value, label: doc.label })))
const comboCount = (doc: string, stance: string, source: string) => comboCounts.value.find(c => c.doc_type === doc && c.stance === stance && c.source === source)?.count ?? 0
const currentVersion = ref<number | null>(null)
const listLoading = ref(false)
const listLoaded = ref(false)
const listError = ref('')
const full = computed(() => items.value.length >= MAX_ITEMS)

async function loadList() {
  listLoading.value = true
  listError.value = ''
  try {
    const res = await adminRequirementsApi.list()
    items.value = res.items
    currentVersion.value = res.version
    comboCounts.value = res.combo_counts
  } catch (e) {
    listError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    listLoading.value = false
    listLoaded.value = true
  }
}

const formRef = ref<FormInstance>()
const dialog = ref(false)
const editing = ref<CompanyReqRow | null>(null)
const submitting = ref(false)
const form = reactive<CompanyReqBody>({ text: '', doc_types: ['all'], stances: ['all'], sources: ['all'], enabled: true, sort: 0 })
const rules: FormRules = {
  text: [
    { required: true, whitespace: true, message: '请输入内容', trigger: 'blur' },
    { max: 300, message: '内容不能超过 300 字', trigger: 'blur' },
  ],
}

function openCreate() {
  editing.value = null
  Object.assign(form, { text: '', doc_types: ['all'], stances: ['all'], sources: ['all'], enabled: true, sort: 0 })
  dialog.value = true
  formRef.value?.clearValidate()
}

function openEdit(row: CompanyReqRow) {
  editing.value = row
  Object.assign(form, {
    text: row.text,
    doc_types: [...row.doc_types],
    stances: [...row.stances],
    sources: [...row.sources],
    enabled: row.enabled,
    sort: row.sort,
  })
  dialog.value = true
  formRef.value?.clearValidate()
}

/** “全部”与具体值互斥：新选了“全部”则只留“全部”，选了具体值则去掉“全部”，清空时回到“全部”。 */
function normalizeScope(key: 'doc_types' | 'stances' | 'sources', value: string[]) {
  const previous = form[key]
  let next = value
  if (next.includes('all') && !previous.includes('all')) next = ['all']
  else if (next.includes('all') && next.length > 1) next = next.filter((v) => v !== 'all')
  form[key] = next.length ? next : ['all']
}

async function submit() {
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    const body = { ...form, text: form.text.trim() }
    if (editing.value) await adminRequirementsApi.update(editing.value.id, body)
    else await adminRequirementsApi.create(body)
    ElMessage.success('已保存')
    dialog.value = false
    await Promise.all([loadList(), loadVersions()])
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  } finally {
    submitting.value = false
  }
}

async function toggleEnabled(row: CompanyReqRow, value: boolean) {
  try {
    await adminRequirementsApi.update(row.id, {
      text: row.text,
      doc_types: row.doc_types,
      stances: row.stances,
      sources: row.sources,
      enabled: value,
      sort: row.sort,
    })
    ElMessage.success(value ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  }
  await Promise.all([loadList(), loadVersions()])
}

async function remove(row: CompanyReqRow) {
  try {
    await ElMessageBox.confirm(
      `将删除公司补充要求 ${row.code}。已完成的审核记录不受影响，编号不会再被使用，删除后无法恢复。`,
      '删除公司补充要求',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await adminRequirementsApi.remove(row.id)
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error(errText(e, '删除失败，请稍后重试'))
  }
  await Promise.all([loadList(), loadVersions()])
}

// ---- 按文件类型分栏的条款卡（二版 6.23）：卡上有启用开关；拖动排序，键盘用“上移 / 下移”，都写回 sort ----
const DOC_ORDER = Object.keys(DOC_TYPE_OPTIONS)
const colOf = (r: CompanyReqRow) => r.doc_types.includes('all') ? 'all' : DOC_ORDER.find(d => r.doc_types.includes(d)) ?? 'all'
const columns = computed(() => ['all', ...DOC_ORDER].map(key => ({
  key, title: key === 'all' ? '全部文件类型' : docTypeLabel(key), items: items.value.filter(r => colOf(r) === key),
})).filter(c => c.items.length))
const extraTypes = (r: CompanyReqRow) => r.doc_types.includes('all') ? '' : r.doc_types.filter(d => d !== colOf(r)).map(docTypeLabel).join('、')
const reorderBusy = ref(false)
async function persistOrder(list: CompanyReqRow[]) {
  reorderBusy.value = true
  try {
    for (const [i, r] of list.entries()) {
      const sort = (i + 1) * 10
      if (r.sort !== sort) await adminRequirementsApi.update(r.id, { text: r.text, doc_types: r.doc_types, stances: r.stances, sources: r.sources, enabled: r.enabled, sort })
    }
  } catch (e) { ElMessage.error(errText(e, '排序保存失败，请稍后重试')) }
  await Promise.all([loadList(), loadVersions()])
  reorderBusy.value = false
}
function moveBy(list: CompanyReqRow[], r: CompanyReqRow, step: -1 | 1) {
  const from = list.indexOf(r), to = from + step
  if (reorderBusy.value || to < 0 || to >= list.length) return
  const next = [...list]
  next.splice(from, 1)
  next.splice(to, 0, r)
  void persistOrder(next)
}
const dragging = ref<CompanyReqRow | null>(null), dropAt = ref<string | number | null>(null)
function dropOn(list: CompanyReqRow[], target: CompanyReqRow) {
  const from = dragging.value
  dragging.value = null
  dropAt.value = null
  if (!from || from === target || reorderBusy.value || !list.includes(from)) return
  const next = list.filter(x => x !== from)
  next.splice(next.indexOf(target) + (list.indexOf(from) < list.indexOf(target) ? 1 : 0), 0, from)
  void persistOrder(next)
}

// ---- ③ 公司说明 ----
const profile = ref('')
const profileLoading = ref(false)
const profileBase = ref('')
const profileError = ref('')

async function loadProfile() {
  profileLoading.value = true
  profileError.value = ''
  try {
    profile.value = profileBase.value = (await adminRequirementsApi.getProfile()).text
  } catch (e) {
    profileError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    profileLoading.value = false
  }
}

const profileDirty = () => !profileLoading.value && profile.value !== profileBase.value
async function saveProfile() {
  if (!profileDirty()) return
  const text = profile.value.trim()
  profileBase.value = (await adminRequirementsApi.saveProfile(text)).text
  if (profile.value.trim() === text) profile.value = profileBase.value
  await Promise.all([loadList(), loadVersions()])
}
// 公司说明：停顿 1 秒或离开输入框后自动保存（V31-U5F-06 17）
const profileSave = useAutoSave(saveProfile)
watch(profile, () => { if (profileDirty()) profileSave.schedule() })

// ---- ④ 版本历史 ----
const versions = ref<RequirementVersionRow[]>([])
const versionsLoading = ref(false)
const versionsError = ref('')
const viewing = ref<RequirementVersionRow | null>(null)

async function loadVersions() {
  versionsLoading.value = true
  versionsError.value = ''
  try {
    versions.value = (await adminRequirementsApi.versions()).items
  } catch (e) {
    versionsError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    versionsLoading.value = false
  }
}

onMounted(() => {
  void loadBuiltin()
  void loadList()
  void loadProfile()
  void loadVersions()
})
</script>

<template>
  <div class="settings-page">
    <div class="panel setting-panel">
      <SettingSection title="公司说明" description="简述公司主营与出口合规情况，随每次审核发送给模型。">
      <SiNotice v-if="profileError" type="error" :closable="false" :title="profileError" />
      <div v-loading="profileLoading">
        <el-input
          v-model="profile"
          type="textarea"
          :rows="4"
          maxlength="300"
          show-word-limit
          placeholder="如公司主营产品、主要用途和市场、出口合规要求。可为空。"
          @blur="profileSave.flush()"
        />
        <div class="hint tip">公司说明会随每次审核发送给模型，请勿写入具体客户名称、电话、账号或金额。<SaveState :status="profileSave.status.value" :message="profileSave.message.value" @retry="profileSave.retry()" /></div>
      </div>
    </SettingSection>
      <SettingSection title="内置要求" description="只读；按文件类型查看系统自带的审核要求。" stacked>
      <div class="filters">
        <el-select aria-label="全部文件类型" v-model="builtinType" clearable placeholder="全部文件类型" class="type-filter" @change="loadBuiltin">
          <el-option v-for="o in toOptions(DOC_TYPE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <span class="hint version">内置要求版本：{{ builtinVersion || '—' }}，共 {{ builtin.length }} 条</span>
      </div>
      <SiNotice v-if="builtinError" type="error" :closable="false" :title="builtinError" />
      <el-table v-loading="builtinLoading" :data="builtin" size="small" max-height="360" empty-text="没有符合条件的内置要求">
        <el-table-column prop="id" label="编号" width="90" class-name="num" />
        <el-table-column label="维度" width="120">
          <template #default="{ row }">{{ row.dimension ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="text" label="内容" min-width="280" />
        <el-table-column label="适用文件类型" width="200">
          <template #default="{ row }">{{ scopeText(row.doc_types, docTypeLabel) }}</template>
        </el-table-column>
      </el-table>
    </SettingSection>
      <SettingSection title="公司补充要求" description="在内置要求之外增加本公司的审核内容；只能增加，不能取消内置要求。" stacked>
      <SiSegmented v-model="companyFilter" :options="companyFilters" ariaLabel="公司补充要求筛选" />
      <SharedContent v-if="companyFilter === 'shared'" kind="bid" />
      <template v-else>
      <div class="hint tip">补充要求只能增加审核内容，不能取消内置要求。请勿在补充要求中写入具体客户名称。</div>
      <details class="combo"><summary>各组合生效条数</summary>
        <el-table v-loading="listLoading" :data="comboRows" size="small" class="combo-table">
          <el-table-column prop="label" label="文件类型" width="110" />
          <el-table-column v-for="col in comboColumns" :key="col.stance + col.source" :label="col.label" min-width="120">
            <template #default="{ row }"><span :class="{ warning: comboCount(row.doc_type, col.stance, col.source) >= 25 }">{{ comboCount(row.doc_type, col.stance, col.source) }} / 30</span></template>
          </el-table-column>
        </el-table>
      </details>
      <div class="filters">
        <el-button type="primary" :disabled="full" @click="openCreate"><template #icon><Plus :size="16" :stroke-width="1.75" /></template>新增要求</el-button>
        <span v-if="full" class="hint version">已达 {{ MAX_ITEMS }} 条上限，请先删除不用的条目。</span>
        <span v-else class="hint version">已有 {{ items.length }} / {{ MAX_ITEMS }} 条 · 拖动卡片调整顺序，键盘用上移、下移</span>
      </div>
      <SiNotice v-if="listError" type="error" :closable="false" :title="listError" />
      <div v-if="!listLoaded" class="req-skel si-wait" aria-busy="true"><i v-for="n in 3" :key="n" /></div>
      <EmptyState v-else-if="!items.length" compact title="尚未添加公司补充要求" description="点击上方“新增要求”，补充的要求会并入每次审核。" />
      <div v-else v-loading="listLoading" class="reqcols">
        <div v-for="col in columns" :key="col.key" class="reqcol">
          <h5><FileText :size="14" :stroke-width="1.75" />{{ col.title }}<SiBadge>{{ col.items.length }}</SiBadge></h5>
          <div v-for="(r, i) in col.items" :key="r.id" class="reqc" :class="{ off: !r.enabled, drop: dropAt === r.id && dragging !== r }" draggable="true"
            @dragstart="dragging = r" @dragend="dragging = null; dropAt = null" @dragover.prevent="dropAt = r.id" @dragleave="dropAt === r.id && (dropAt = null)" @drop.prevent="dropOn(col.items, r)">
            <span class="gr" aria-hidden="true"><GripVertical :size="16" :stroke-width="1.75" /></span>
            <div class="rq-b">
              <b>{{ r.text }}</b>
              <div class="rq-t">
                <SiBadge tone="brand">{{ r.stances.includes('all') ? '通用' : scopeText(r.stances, stanceLabel) }}</SiBadge>
                <SiBadge v-if="!r.sources.includes('all')" plain>{{ scopeText(r.sources, sourceLabel) }}</SiBadge>
                <SiBadge v-if="extraTypes(r)" plain>另含{{ extraTypes(r) }}</SiBadge>
                <span class="code num">{{ r.code }}</span>
              </div>
              <div class="rq-a">
                <button type="button" class="ibtn" title="上移" :aria-label="`上移 ${r.code}`" :disabled="i === 0 || reorderBusy" @click="moveBy(col.items, r, -1)"><ChevronUp :size="15" :stroke-width="1.75" /></button>
                <button type="button" class="ibtn" title="下移" :aria-label="`下移 ${r.code}`" :disabled="i === col.items.length - 1 || reorderBusy" @click="moveBy(col.items, r, 1)"><ChevronDown :size="15" :stroke-width="1.75" /></button>
                <RowMore><el-dropdown-item @click="openEdit(r)">编辑</el-dropdown-item><el-dropdown-item class="danger-item" @click="remove(r)">删除</el-dropdown-item></RowMore>
              </div>
            </div>
            <el-switch size="small" :model-value="r.enabled" :aria-label="`启用 ${r.code}`" @change="(v: string | number | boolean) => toggleEnabled(r, v === true)" />
          </div>
        </div>
      </div>
      </template>
    </SettingSection>
      <SettingSection title="发送本地财务比率" description="可选择把本机计算的比率用于模型分析，默认关闭。"><RatioSetting /></SettingSection>
      <SettingSection title="版本历史" description="公司说明与补充要求每次修改的记录。" stacked>
      <SiNotice v-if="versionsError" type="error" :closable="false" :title="versionsError" />
      <el-table v-loading="versionsLoading" :data="versions" size="small" max-height="320" empty-text="尚无修改记录">
        <el-table-column label="版本号" width="100" class-name="num">
          <template #default="{ row }">第 {{ row.version }} 版</template>
        </el-table-column>
        <el-table-column label="修改人" min-width="160">
          <template #default="{ row }">{{ row.created_by_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="160" class-name="num">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewing = row">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </SettingSection>
    </div>

    <el-dialog v-model="dialog" :title="editing ? `编辑 ${editing.code}` : '新增公司补充要求'" width="640px" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="140px" @submit.prevent="submit">
        <el-form-item label="内容" prop="text">
          <el-input v-model="form.text" type="textarea" :rows="4" maxlength="300" show-word-limit />
        </el-form-item>
        <el-form-item label="适用文件类型">
          <el-select :model-value="form.doc_types" multiple class="scope-select" @update:model-value="(v: string[]) => normalizeScope('doc_types', v)">
            <el-option label="全部" value="all" />
            <el-option v-for="o in toOptions(DOC_TYPE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用立场">
          <el-select :model-value="form.stances" multiple class="scope-select" @update:model-value="(v: string[]) => normalizeScope('stances', v)">
            <el-option label="全部" value="all" />
            <el-option v-for="o in toOptions(STANCE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用来源">
          <el-select :model-value="form.sources" multiple class="scope-select" @update:model-value="(v: string[]) => normalizeScope('sources', v)">
            <el-option label="全部" value="all" />
            <el-option v-for="o in toOptions(SOURCE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="排序（0–9999）">
          <el-input-number v-model="form.sort" :min="0" :max="9999" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog :model-value="viewing !== null" :title="viewing ? `第 ${viewing.version} 版` : ''" width="640px" @close="viewing = null">
      <template v-if="viewing">
        <h3 class="first">当时启用的公司补充要求（{{ viewing.snapshot.requirements.length }} 条）</h3>
        <EmptyState v-if="!viewing.snapshot.requirements.length" compact title="当时没有启用的补充要求" />
        <ul class="snapshot">
          <li v-for="r in viewing.snapshot.requirements" :key="r.code">
            <b>{{ r.code }}</b>　{{ r.text }}
            <div class="hint">
              文件类型：{{ scopeText(r.doc_types, docTypeLabel) }}；立场：{{ scopeText(r.stances, stanceLabel) }}；来源：{{ scopeText(r.sources ?? ['all'], sourceLabel) }}
            </div>
          </li>
        </ul>
        <h3>公司说明</h3>
        <pre class="plain">{{ viewing.snapshot.company_profile || '（空）' }}</pre>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
h3 {
  margin: 20px 0 16px;
  font: var(--si-font-h2);
}
h3.first {
  margin-top: 0;
}
.tip {
  margin: 4px 0 8px;
}
.version {
  align-self: center;
}
.snapshot {
  margin: 0;
  padding-left: 20px;
  line-height: 1.7;
}
.plain {
  margin: 0;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
}
.readonly-panel { background: var(--el-fill-color-light); }
.type-filter { width: 180px; }
.scope-select { width: 100%; }
.filters { flex-wrap: wrap; }
.warning { color: var(--el-color-warning); }
.combo { margin: 4px 0 12px; }
.combo summary { cursor: pointer; color: var(--si-ink-3); font: var(--si-font-meta); }
.combo-table { margin: 8px 0 4px; }
.req-skel { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.req-skel i { display: block; height: 120px; border-radius: var(--si-radius-card); background: var(--si-hover-bg); }
.reqcols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-top: 12px; align-items: start; }
.reqcol h5 { display: flex; align-items: center; gap: 6px; margin: 0 0 8px 2px; font: var(--si-font-label-sm); font-weight: 600; color: var(--si-ink-3); }
.reqc { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; padding: 12px 12px 10px; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); transition: box-shadow var(--si-d2) var(--si-ease-out); }
.reqc:hover { box-shadow: var(--si-shadow-2); }
.reqc.drop { box-shadow: 0 -3px 0 var(--si-brand-2), var(--si-shadow-2); }
.reqc .gr { padding-top: 2px; color: var(--si-ink-4); cursor: grab; }
.rq-b { flex: 1; min-width: 0; }
.rq-b > b { display: block; color: var(--si-ink-1); font: var(--si-font-label); line-height: 21px; overflow-wrap: anywhere; }
.reqc.off .rq-b > b { color: var(--si-ink-4); }
.rq-t { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.code { margin-left: auto; color: var(--si-ink-4); font-size: 12px; }
.rq-a { display: flex; align-items: center; gap: 2px; margin-top: 6px; margin-left: -6px; }
.ibtn { display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.ibtn:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.ibtn:disabled { opacity: .35; cursor: default; }
</style>
