<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadUserFile } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { kbApi, type KbDoc, type KbChunk } from '@/api/kb'
import { ApiError } from '@/api/http'
import { KB_STATUS, KB_STATUS_TAG } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const rows = ref<KbDoc[]>([]), enabled = ref(false), loading = ref(false), saving = ref(false), error = ref('')
const uploading = ref(false), uploadOpen = ref(false), formError = ref(''), files = ref<UploadUserFile[]>([])
const previewOpen = ref(false), previewLoading = ref(false), previewError = ref(''), viewing = ref(''), chunks = ref<KbChunk[]>([])
let active = true, timer: ReturnType<typeof setTimeout> | undefined, previewVersion = 0
const err = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
function schedule() {
  clearTimeout(timer)
  if (active && rows.value.some(row => row.status === 'parsing')) timer = setTimeout(() => load(), 3000)
}
async function load() {
  if (loading.value || saving.value || uploading.value) { schedule(); return }
  loading.value = true
  try { const data = await kbApi.list(); if (active) { rows.value = data.items; enabled.value = data.enabled; error.value = '' } }
  catch (e) { if (active) error.value = `加载失败：${err(e)}。请刷新页面后重试。` }
  finally { if (active) { loading.value = false; schedule() } }
}
async function switchGlobal(value: string | number | boolean) {
  saving.value = true; error.value = ''
  try { enabled.value = (await kbApi.setting(Boolean(value))).enabled; ElMessage.success('知识库设置已保存') }
  catch (e) { error.value = err(e) }
  finally { saving.value = false; schedule() }
}
function openUpload() { files.value = []; formError.value = ''; uploadOpen.value = true }
async function upload() {
  const selected = files.value.map(item => item.raw).filter((file): file is NonNullable<typeof file> => !!file)
  if (!selected.length) { formError.value = '请选择知识库文件'; return }
  for (const file of selected) {
    if (!/\.(docx|pdf|xlsx|doc|wps|rtf|xls|et)$/i.test(file.name)) { formError.value = '支持 DOCX、PDF、XLSX 及对应旧格式'; return }
    if (!file.size || file.size > 20 * 1024 * 1024) { formError.value = '请选择非空文件，单个文件不超过 20 MB'; return }
  }
  uploading.value = true; formError.value = ''; let completed = 0
  try {
    for (const file of selected) { await kbApi.upload(file); completed++; files.value.shift() }
    uploadOpen.value = false; ElMessage.success(`已上传 ${completed} 份文档，正在本机解析`)
  } catch (e) { formError.value = `${completed ? `已上传 ${completed} 份；` : ''}${err(e)}` }
  finally { uploading.value = false; await load() }
}
async function toggle(row: KbDoc, value: string | number | boolean) {
  saving.value = true; error.value = ''
  try { Object.assign(row, await kbApi.patch(row.id, { enabled: Boolean(value) })); ElMessage.success('文档启用状态已保存') }
  catch (e) { error.value = err(e) }
  finally { saving.value = false; schedule() }
}
async function rename(row: KbDoc) {
  try {
    const { value } = await ElMessageBox.prompt('输入文档名称，最多 255 字', '修改文档名称', { inputValue: row.name,
      inputValidator: value => !!value.trim() && value.trim().length <= 255 || '请输入 1～255 字名称', confirmButtonText: '保存', cancelButtonText: '取消' })
    saving.value = true
    Object.assign(row, await kbApi.patch(row.id, { name: value.trim() })); ElMessage.success('文档名称已保存')
  } catch (e) { if (e instanceof ApiError) error.value = e.message }
  finally { saving.value = false; schedule() }
}
async function remove(row: KbDoc) {
  try { await ElMessageBox.confirm(`将删除“${row.name}”及其全部片段和向量，无法恢复。`, '删除知识库文档', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  saving.value = true; error.value = ''
  try { await kbApi.remove(row.id); ElMessage.success('知识库文档已删除'); if (viewing.value === row.id) previewOpen.value = false }
  catch (e) { error.value = err(e) }
  finally { saving.value = false; await load() }
}
async function preview(row: KbDoc) {
  const current = ++previewVersion
  previewOpen.value = true; previewLoading.value = true; previewError.value = ''; chunks.value = []; viewing.value = row.id
  try { const data = await kbApi.chunks(row.id); if (active && current === previewVersion) chunks.value = data.items }
  catch (e) { if (active && current === previewVersion) previewError.value = err(e) }
  finally { if (active && current === previewVersion) previewLoading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { active = false; ++previewVersion; clearTimeout(timer) })
</script>
<template>
  <div>
    <PageHeader title="公司知识库" description="上传公司制度、流程与产品资料。员工在智能助手中勾选后，相关片段经脱敏后随问题发送给模型。"><el-button type="primary" :disabled="saving || uploading" @click="openUpload">上传文档</el-button></PageHeader>
    <section v-loading="loading" class="panel">
      <div class="setting-row"><span>在智能助手中启用知识库</span><el-switch :model-value="enabled" :disabled="saving || loading || uploading" aria-label="在智能助手中启用知识库" @change="switchGlobal" /></div>
      <p class="hint">关闭后不会检索知识库；只有启用且解析完成的文档参与检索。</p>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="gap" />
      <el-table :data="rows" empty-text="尚无知识库文档，可上传公司制度、流程与产品资料">
        <el-table-column label="名称" min-width="180"><template #default="{ row }">{{ row.name }}<p v-if="row.error_message" class="error">{{ row.error_message }}</p></template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="KB_STATUS_TAG[row.status]" size="small">{{ KB_STATUS[row.status] }}</el-tag></template></el-table-column>
        <el-table-column prop="chunk_count" label="片段数" width="80" class-name="num" />
        <el-table-column label="启用" width="80"><template #default="{ row }"><el-switch :model-value="row.enabled" :disabled="saving || uploading" :aria-label="`启用${row.name}`" @change="(value: string | number | boolean) => toggle(row, value)" /></template></el-table-column>
        <el-table-column prop="uploaded_by" label="上传人" min-width="90" />
        <el-table-column label="时间" width="170" class-name="num"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" min-width="180"><template #default="{ row }"><el-button link type="primary" :disabled="saving" @click="preview(row)">预览片段</el-button><el-button link type="primary" :disabled="saving" @click="rename(row)">改名</el-button><el-button link type="primary" :disabled="saving || uploading" @click="toggle(row, !row.enabled)">{{ row.enabled ? '停用' : '启用' }}</el-button><el-button link type="danger" :disabled="saving || uploading" @click="remove(row)">删除</el-button></template></el-table-column>
      </el-table>
    </section>
    <el-dialog v-model="uploadOpen" title="上传知识库文档" width="640px" :close-on-click-modal="!uploading" :close-on-press-escape="!uploading" :show-close="!uploading">
      <p class="hint">支持 DOCX、PDF、XLSX 及对应旧格式，单个文件不超过 20 MB。相同内容不能重复上传。</p>
      <el-upload v-model:file-list="files" multiple :auto-upload="false" :disabled="uploading" accept=".docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et"><el-button :disabled="uploading">选择文件</el-button></el-upload>
      <el-alert v-if="formError" :title="formError" type="error" show-icon :closable="false" class="gap" />
      <template #footer><el-button :disabled="uploading" @click="uploadOpen = false">取消</el-button><el-button type="primary" :loading="uploading" @click="upload">上传并解析</el-button></template>
    </el-dialog>
    <el-dialog v-model="previewOpen" title="预览知识库片段" width="960px" @closed="previewVersion++">
      <div v-loading="previewLoading" class="preview"><p class="hint">最多预览前 200 段。名称、标题和正文仅在本机显示。</p><el-alert v-if="previewError" :title="previewError" type="error" :closable="false" /><section v-for="chunk in chunks" :key="chunk.id" class="chunk"><h3><span class="num">{{ chunk.seq }}</span> {{ chunk.heading }}</h3><pre>{{ chunk.text }}</pre></section><el-empty v-if="!previewLoading && !chunks.length && !previewError" description="尚无可预览片段，请等待解析完成" /></div>
    </el-dialog>
  </div>
</template>
<style scoped>
.setting-row { display: flex; gap: 16px; align-items: center; margin-bottom: 12px; }
.preview { min-height: 120px; max-height: 65vh; overflow-y: auto; }
.chunk { border-bottom: 1px solid var(--el-border-color-light); padding: 16px 0; }
.chunk h3 { font: var(--si-font-h3); margin: 0 0 12px; }
pre { font: var(--si-font-reading); white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
.error { color: var(--el-color-danger); font: var(--si-font-meta); }
</style>
