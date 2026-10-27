<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadUserFile } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { ApiError } from '@/api/http'
import { templatesApi, type TemplateRow, type TemplateDetail, type TemplateClause } from '@/api/templates'
import { DOC_TYPE_OPTIONS, STANCE_OPTIONS, docTypeLabel, stanceLabel, toOptions } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const rows = ref<TemplateRow[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const dialog = ref(false)
const editing = ref<string | null>(null)
const form = reactive({ name: '', doc_types: [] as string[], stances: [] as string[] })
const files = ref<UploadUserFile[]>([])
const formError = ref('')
const viewing = ref<TemplateDetail | null>(null)
const detailOpen = ref(false)
const detailError = ref('')
const detailLoading = ref(false)
const err = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
let timer: ReturnType<typeof setInterval> | null = null
let active = true
async function load(silent = false) {
  if (loading.value) return
  loading.value = true
  try { const result = await templatesApi.list(); if (active) { rows.value = result.items; error.value = '' } }
  catch (e) { if (!silent) error.value = `加载失败：${err(e)}。请刷新页面后重试。` }
  finally { loading.value = false }
}
function open(row?: TemplateRow) {
  editing.value = row?.id ?? null; form.name = row?.name ?? ''
  form.doc_types = [...(row?.doc_types ?? [])]; form.stances = [...(row?.stances ?? [])]
  files.value = []; formError.value = ''; dialog.value = true
}
async function save() {
  if (!form.name.trim() || !form.doc_types.length || !form.stances.length) { formError.value = '请填写名称并选择文件类型和立场'; return }
  const file = files.value[0]?.raw
  if (!editing.value && (!file || !file.name.toLowerCase().endsWith('.docx'))) { formError.value = '请选择 DOCX 范本文件'; return }
  saving.value = true; formError.value = ''
  try {
    if (editing.value) await templatesApi.patch(editing.value, form)
    else if (file) await templatesApi.upload(form.name.trim(), form.doc_types, form.stances, file)
    dialog.value = false; ElMessage.success(editing.value ? '已保存范本设置' : '范本已上传，正在准备向量'); await load()
  } catch (e) { formError.value = err(e) }
  finally { saving.value = false }
}
async function toggle(row: TemplateRow, value: string | number | boolean) {
  saving.value = true
  try { Object.assign(row, await templatesApi.patch(row.id, { enabled: Boolean(value) })); ElMessage.success('已保存') }
  catch (e) { ElMessage.error(err(e)) }
  finally { saving.value = false }
}
async function remove(row: TemplateRow) {
  try { await ElMessageBox.confirm(`删除“${row.name}”及其全部条款和向量？旧轮次会保留当时的名称与结果。此操作无法恢复。`, '删除公司范本', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  saving.value = true
  try { await templatesApi.remove(row.id); ElMessage.success('已删除范本'); if (viewing.value?.id === row.id) detailOpen.value = false; await load() }
  catch (e) { ElMessage.error(err(e)) }
  finally { saving.value = false }
}
async function detail(row: TemplateRow) {
  detailOpen.value = true; detailLoading.value = true; detailError.value = ''; viewing.value = null
  try { viewing.value = await templatesApi.get(row.id) }
  catch (e) { detailError.value = err(e) }
  finally { detailLoading.value = false }
}
async function saveClause(clause: TemplateClause) {
  if (!viewing.value) return
  saving.value = true; detailError.value = ''
  try {
    Object.assign(clause, await templatesApi.clause(viewing.value.id, clause.id, { stance: clause.stance, acceptable: clause.acceptable, escalate: clause.escalate }))
    ElMessage.success('条款说明已保存')
  } catch (e) { detailError.value = err(e) }
  finally { saving.value = false }
}
onMounted(() => {
  void load()
  timer = setInterval(() => { if (!saving.value && rows.value.some(r => !r.ready)) void load(true) }, 3000)
})
onBeforeUnmount(() => { active = false; if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <PageHeader title="公司范本" description="维护公司标准合同，审核时按文件类型和立场选择合适的范本。">
      <el-button type="primary" :disabled="rows.length >= 50 || saving" @click="open()">上传公司范本</el-button>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="gap" />
    <section v-loading="loading" class="panel">
      <p class="hint">最多 50 份，含停用范本。只有启用且向量准备完成的范本参与选择；更换文件请删除后重新上传。</p>
      <el-table :data="rows" empty-text="尚无公司范本，可上传 DOCX 标准合同">
        <el-table-column prop="name" label="范本名称" min-width="180" />
        <el-table-column label="文件类型" min-width="130"><template #default="{ row }">{{ row.doc_types.map(docTypeLabel).join('、') }}</template></el-table-column>
        <el-table-column label="立场" min-width="100"><template #default="{ row }">{{ row.stances.map(stanceLabel).join('、') }}</template></el-table-column>
        <el-table-column prop="clause_count" label="条款数" width="80" class-name="num" />
        <el-table-column label="向量状态" width="100"><template #default="{ row }">{{ row.ready ? '可用' : '准备中' }}</template></el-table-column>
        <el-table-column label="启用" width="80"><template #default="{ row }"><el-switch :model-value="row.enabled" :disabled="saving" :aria-label="`启用${row.name}`" @change="(value: string | number | boolean) => toggle(row, value)" /></template></el-table-column>
        <el-table-column label="操作" min-width="180"><template #default="{ row }"><el-button link type="primary" :disabled="saving" @click="detail(row)">查看条款</el-button><el-button link type="primary" :disabled="saving" @click="open(row)">编辑</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template></el-table-column>
      </el-table>
    </section>
    <el-dialog v-model="dialog" :title="editing ? '编辑范本设置' : '上传公司范本'" width="640px" :close-on-click-modal="!saving">
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" class="gap" />
      <el-form label-position="top">
        <el-form-item label="范本名称"><el-input v-model="form.name" maxlength="100" show-word-limit /></el-form-item>
        <el-form-item label="适用文件类型"><el-select v-model="form.doc_types" multiple placeholder="请选择文件类型"><el-option v-for="o in toOptions(DOC_TYPE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" /></el-select></el-form-item>
        <el-form-item label="适用立场"><el-select v-model="form.stances" multiple placeholder="请选择立场"><el-option v-for="o in toOptions(STANCE_OPTIONS)" :key="o.value" :label="o.label" :value="o.value" /></el-select></el-form-item>
        <template v-if="!editing"><p class="hint">DOCX 文件，1–200 个一级条款，每条最多 5,000 字。名称和条款在本地加密保存。</p><el-upload v-model:file-list="files" :auto-upload="false" :limit="1" accept=".docx" :disabled="saving" @exceed="ElMessage.error('每次只能上传一份范本')"><el-button>选择 DOCX 文件</el-button></el-upload></template>
      </el-form>
      <template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ editing ? '保存设置' : '上传并拆分条款' }}</el-button></template>
    </el-dialog>
    <el-dialog v-model="detailOpen" title="范本条款" width="640px" :close-on-click-modal="!saving">
      <div v-loading="detailLoading">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
        <template v-if="viewing">
          <h2>{{ viewing.name }}</h2><p class="hint">更新时间：{{ formatDateTime(viewing.updated_at) }} · {{ viewing.clause_count }} 条</p>
          <el-collapse><el-collapse-item v-for="clause in viewing.clauses" :key="clause.id" :name="clause.id" :title="`${clause.seq}. ${clause.title}`">
            <pre class="plain">{{ clause.text }}</pre>
            <el-form label-position="top">
              <el-form-item label="标准立场"><el-input v-model="clause.stance" type="textarea" maxlength="300" show-word-limit /></el-form-item>
              <el-form-item label="可接受范围"><el-input v-model="clause.acceptable" type="textarea" maxlength="300" show-word-limit /></el-form-item>
              <el-form-item label="需上报的情形"><el-input v-model="clause.escalate" type="textarea" maxlength="300" show-word-limit /></el-form-item>
            </el-form><div class="actions"><el-button type="primary" :loading="saving" @click="saveClause(clause)">保存条款说明</el-button></div>
          </el-collapse-item></el-collapse>
        </template>
      </div>
      <template #footer><el-button :disabled="saving" @click="detailOpen = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
:deep(.el-dialog__title), :deep(.el-drawer__title) { font: var(--si-font-h2); }
:deep(.el-dialog__body), :deep(.el-drawer__body) { font: var(--si-font-body); }

h2 { font: var(--si-font-h2); margin: 0 0 16px; overflow-wrap: anywhere; }
.plain { font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; background: var(--el-fill-color-light); padding: 12px; border-left: 3px solid var(--el-border-color); }
</style>
