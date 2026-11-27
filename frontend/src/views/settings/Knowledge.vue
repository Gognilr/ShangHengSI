<script setup lang="ts">
import SiBadge from '@/components/ui/SiBadge.vue'
import SiNotice from '@/components/ui/SiNotice.vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadUserFile } from 'element-plus'
import EmptyState from '@/components/EmptyState.vue'
import RowMore from '@/components/RowMore.vue'
import PaperThumb from '@/components/settings/PaperThumb.vue'
import SettingSection from '@/components/SettingSection.vue'
import { kbApi, type KbDoc, type KbChunk } from '@/api/kb'
import { ApiError } from '@/api/http'
import { KB_STATUS, KB_STATUS_TAG } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const loaded = ref(false)
const rows = ref<KbDoc[]>([]), enabled = ref(false), loading = ref(false), saving = ref(false), error = ref('')
const uploading = ref(false), uploadOpen = ref(false), formError = ref(''), files = ref<UploadUserFile[]>([])
const retryTarget = ref<KbDoc | null>(null)
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
  finally { if (active) { loading.value = false; loaded.value = true; schedule() } }
}
async function switchGlobal(value: string | number | boolean) {
  saving.value = true; error.value = ''
  try { enabled.value = (await kbApi.setting(Boolean(value))).enabled; ElMessage.success('知识库设置已保存') }
  catch (e) { error.value = err(e) }
  finally { saving.value = false; schedule() }
}
function openUpload(row?: KbDoc) {
  retryTarget.value = row ?? null
  files.value = []; formError.value = ''; uploadOpen.value = true
}
async function upload() {
  const selected = files.value.map(item => item.raw).filter((file): file is NonNullable<typeof file> => !!file)
  if (!selected.length) { formError.value = '请选择知识库文件'; return }
  for (const file of selected) {
    if (!/\.(docx|pdf|xlsx|doc|wps|rtf|xls|et)$/i.test(file.name)) { formError.value = '支持 DOCX、PDF、XLSX 及对应旧格式'; return }
    if (!file.size || file.size > 20 * 1024 * 1024) { formError.value = '请选择非空文件，单个文件不超过 20 MB'; return }
  }
  uploading.value = true; formError.value = ''; let completed = 0
  try {
    const retryId = retryTarget.value?.id
    for (const file of selected) { await kbApi.upload(file, retryId); completed++; files.value.shift() }
    uploadOpen.value = false
    ElMessage.success(retryId ? '已重新上传，正在本机解析' : `已上传 ${completed} 份文档，正在本机解析`)
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
    <div v-loading="loading" class="panel setting-panel">
      <SettingSection title="知识库开关" description="关闭后工作台不检索知识库。开启时，员工勾选后相关片段经脱敏随问题发送给模型。">
        <div class="switch-line"><span>在工作台中启用知识库</span><el-switch :model-value="enabled" :disabled="saving || loading || uploading" aria-label="在智能助手中启用知识库" @change="switchGlobal" /></div>
        <p class="hint">关闭后不会检索知识库；只有启用且解析完成的文档参与检索。</p>
      </SettingSection>
      <SiNotice v-if="error" :title="error" type="error" :closable="false" class="gap" />
      <SettingSection title="资料列表" description="只有启用且解析完成的文档参与检索。" stacked><template #actions><el-button type="primary" :disabled="saving || uploading" @click="openUpload()">上传文档</el-button></template>
        <div v-if="!loaded" class="thumbs" aria-busy="true"><i v-for="n in 5" :key="n" class="sk" /></div>
        <EmptyState v-else-if="!rows.length" compact title="尚无知识库文档" description="点击右上角“上传文档”，可上传公司制度、流程与产品资料。" />
        <div v-else class="thumbs">
          <PaperThumb v-for="row in rows" :key="row.id" :name="row.name" :count="row.status === 'ready' ? `片段 ${row.chunk_count}` : undefined" :fail="row.status === 'failed'" :lines="6" :off="!row.enabled" @open="row.status === 'failed' ? openUpload(row) : preview(row)">
            <SiBadge :tone="KB_STATUS_TAG[row.status]">{{ KB_STATUS[row.status] }}</SiBadge>
            <span class="who" :title="`${row.uploaded_by} · ${formatDateTime(row.created_at)}`">{{ row.uploaded_by }} · {{ formatDateTime(row.created_at).slice(5, 10) }}</span>
            <span class="ctl">
              <el-button v-if="row.status === 'failed'" size="small" text type="primary" :disabled="saving || uploading" @click="openUpload(row)">重新上传</el-button>
              <el-switch v-else size="small" :model-value="row.enabled" :disabled="saving || uploading" :aria-label="`启用${row.name}`" @change="(value: string | number | boolean) => toggle(row, value)" />
              <RowMore><el-dropdown-item :disabled="saving" @click="preview(row)">预览片段</el-dropdown-item><el-dropdown-item :disabled="saving" @click="rename(row)">改名</el-dropdown-item><el-dropdown-item class="danger-item" :disabled="saving || uploading" @click="remove(row)">删除</el-dropdown-item></RowMore>
            </span>
            <span v-if="row.error_message" class="err">{{ row.error_message }}</span>
          </PaperThumb>
        </div>
      </SettingSection>
    </div>
    <el-dialog v-model="uploadOpen" :title="retryTarget ? '重新上传知识库文档' : '上传知识库文档'" width="640px" :close-on-click-modal="!uploading" :close-on-press-escape="!uploading" :show-close="!uploading">
      <p v-if="retryTarget" class="hint">请选择「{{ retryTarget.name }}」原来上传的同一份文件。</p>
      <p v-else class="hint">支持 DOCX、PDF、XLSX 及对应旧格式，单个文件不超过 20 MB。相同内容不能重复上传。</p>
      <el-upload v-model:file-list="files" :multiple="!retryTarget" :limit="retryTarget ? 1 : undefined" :auto-upload="false" :disabled="uploading" accept=".docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et"><el-button :disabled="uploading">选择文件</el-button></el-upload>
      <SiNotice v-if="formError" :title="formError" type="error" :closable="false" class="gap" />
      <template #footer><el-button :disabled="uploading" @click="uploadOpen = false">取消</el-button><el-button type="primary" :loading="uploading" @click="upload">上传并解析</el-button></template>
    </el-dialog>
    <el-dialog v-model="previewOpen" title="预览知识库片段" width="960px" @closed="previewVersion++">
      <div v-loading="previewLoading" class="preview"><p class="hint">最多预览前 200 段。名称、标题和正文仅在本机显示。</p><SiNotice v-if="previewError" :title="previewError" type="error" :closable="false" /><section v-for="chunk in chunks" :key="chunk.id" class="chunk"><h3><span class="num">{{ chunk.seq }}</span> {{ chunk.heading }}</h3><pre>{{ chunk.text }}</pre></section><EmptyState v-if="!previewLoading && !chunks.length && !previewError" compact tone="neutral" title="还没有可预览的片段" description="解析完成后可在这里预览。" /></div>
    </el-dialog>
  </div>
</template>
<style scoped>
.preview { min-height: 120px; max-height: 65vh; overflow-y: auto; }
.chunk { border-bottom: 1px solid var(--el-border-color-light); padding: 16px 0; }
.chunk h3 { font: var(--si-font-h3); margin: 0 0 12px; }
pre { font: var(--si-font-reading); white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
.switch-line { display: flex; align-items: center; gap: 12px; }
.error { color: var(--el-color-danger); font: var(--si-font-meta); }
.thumbs { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 16px; }
.sk { display: block; aspect-ratio: 1 / 1.25; border-radius: 6px; background: var(--si-hover-bg); }
.who { flex-basis: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ctl { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.err { flex-basis: 100%; color: var(--si-danger-ink); }
</style>
