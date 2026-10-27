<script setup lang="ts">
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
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
import PageHeader from '@/components/PageHeader.vue'

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
const items = ref<CompanyReqRow[]>([])
const comboCounts = ref<{ doc_type: string; stance: string; source: string; count: number }[]>([])
const comboColumns = toOptions(STANCE_OPTIONS).flatMap(stance => toOptions(SOURCE_OPTIONS).map(source => ({ stance: stance.value, source: source.value, label: stance.label + ' / ' + source.label })))
const comboRows = computed(() => toOptions(DOC_TYPE_OPTIONS).map(doc => ({ doc_type: doc.value, label: doc.label })))
const comboCount = (doc: string, stance: string, source: string) => comboCounts.value.find(c => c.doc_type === doc && c.stance === stance && c.source === source)?.count ?? 0
const currentVersion = ref<number | null>(null)
const listLoading = ref(false)
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

// ---- ③ 公司说明 ----
const profile = ref('')
const profileLoading = ref(false)
const profileSaving = ref(false)
const profileError = ref('')

async function loadProfile() {
  profileLoading.value = true
  profileError.value = ''
  try {
    profile.value = (await adminRequirementsApi.getProfile()).text
  } catch (e) {
    profileError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    profileLoading.value = false
  }
}

async function saveProfile() {
  profileSaving.value = true
  try {
    profile.value = (await adminRequirementsApi.saveProfile(profile.value.trim())).text
    ElMessage.success('已保存')
    await Promise.all([loadList(), loadVersions()])
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  } finally {
    profileSaving.value = false
  }
}

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
    <PageHeader title="审核要求" description="内置要求只读；公司补充要求只能增加审核内容，不能取消内置要求" />
    <section class="panel">
      <h3 class="first">公司说明</h3>
      <el-alert v-if="profileError" type="error" :closable="false" show-icon :title="profileError" />
      <div v-loading="profileLoading">
        <el-input
          v-model="profile"
          type="textarea"
          :rows="4"
          maxlength="300"
          show-word-limit
          placeholder="如公司主营产品、主要用途和市场、出口合规要求。可为空。"
        />
        <div class="hint tip">公司说明会随每次审核发送给模型，请勿写入具体客户名称、电话、账号或金额。</div>
      </div>
      <div class="actions">
        <el-button type="primary" :loading="profileSaving" @click="saveProfile">保存</el-button>
      </div>
    </section>

<section class="panel">
      <h3 class="first">公司补充要求</h3>
      <div class="hint tip">补充要求只能增加审核内容，不能取消内置要求。请勿在补充要求中写入具体客户名称。</div>
      <h4>各组合生效条数</h4>
      <el-table v-loading="listLoading" :data="comboRows" size="small" class="combo-table">
        <el-table-column prop="label" label="文件类型" width="110" />
        <el-table-column v-for="col in comboColumns" :key="col.stance + col.source" :label="col.label" min-width="120">
          <template #default="{ row }"><span :class="{ warning: comboCount(row.doc_type, col.stance, col.source) >= 25 }">{{ comboCount(row.doc_type, col.stance, col.source) }} / 30</span></template>
        </el-table-column>
      </el-table>
      <div class="filters">
        <el-button type="primary" :disabled="full" @click="openCreate">新增</el-button>
        <span v-if="full" class="hint version">已达 {{ MAX_ITEMS }} 条上限，请先删除不用的条目。</span>
        <span v-else class="hint version">已有 {{ items.length }} / {{ MAX_ITEMS }} 条</span>
      </div>
      <el-alert v-if="listError" type="error" :closable="false" show-icon :title="listError" />
      <el-table v-loading="listLoading" :data="items" size="small" empty-text="尚未添加公司补充要求">
        <el-table-column prop="code" label="编号" width="80" class-name="num" />
        <el-table-column prop="text" label="内容" min-width="200" />
        <el-table-column label="适用文件类型" width="150">
          <template #default="{ row }">{{ scopeText(row.doc_types, docTypeLabel) }}</template>
        </el-table-column>
        <el-table-column label="适用立场" width="100">
          <template #default="{ row }">{{ scopeText(row.stances, stanceLabel) }}</template>
        </el-table-column>
        <el-table-column label="适用来源" width="100">
          <template #default="{ row }">{{ scopeText(row.sources, sourceLabel) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v: string | number | boolean) => toggleEnabled(row, v === true)" />
          </template>
        </el-table-column>
        <el-table-column prop="sort" label="排序" width="70" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

<section class="panel readonly-panel">
      <h3 class="first">内置要求 <el-tag size="small" type="info">只读</el-tag></h3>
      <div class="filters">
        <el-select v-model="builtinType" clearable placeholder="全部文件类型" class="type-filter" @change="loadBuiltin">
          <el-option v-for="o in toOptions(DOC_TYPE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <span class="hint version">内置要求版本：{{ builtinVersion || '—' }}，共 {{ builtin.length }} 条</span>
      </div>
      <el-alert v-if="builtinError" type="error" :closable="false" show-icon :title="builtinError" />
      <el-table v-loading="builtinLoading" :data="builtin" size="small" max-height="360" empty-text="没有符合条件的内置要求">
        <el-table-column prop="id" label="编号" width="90" class-name="num" />
        <el-table-column label="维度" width="120">
          <template #default="{ row }">{{ row.dimension ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="text" label="内容" min-width="360" />
        <el-table-column label="适用文件类型" width="200">
          <template #default="{ row }">{{ scopeText(row.doc_types, docTypeLabel) }}</template>
        </el-table-column>
      </el-table>
    </section>

<section class="panel">
      <h3 class="first">版本历史</h3>
      <el-alert v-if="versionsError" type="error" :closable="false" show-icon :title="versionsError" />
      <el-table v-loading="versionsLoading" :data="versions" size="small" max-height="320" empty-text="尚无修改记录">
        <el-table-column label="版本号" width="100" class-name="num">
          <template #default="{ row }">第 {{ row.version }} 版</template>
        </el-table-column>
        <el-table-column label="修改人" min-width="160">
          <template #default="{ row }">{{ row.created_by_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="170" class-name="num">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewing = row">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="dialog" :title="editing ? `编辑 ${editing.code}` : '新增公司补充要求'" width="640px" :close-on-click-modal="false">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px" @submit.prevent="submit">
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
        <el-empty v-if="!viewing.snapshot.requirements.length" description="当时没有启用的补充要求" :image-size="48" />
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
.combo-table { margin-bottom: 16px; }
</style>
