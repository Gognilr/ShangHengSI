<script setup lang="ts">
import { computed, ref, onBeforeUnmount, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { assistantApi, confirmationTask, type Session, type SessionFile } from '@/api/assistant'
import { saveBlob } from '@/utils/save'
import type { LocalFinding } from '@/api/localFindings'
import MediaRow from './MediaRow.vue'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import type { Stance } from '@/api/tasks'
import { ApiError } from '@/api/http'
import { assistantStatusLabel, statusTag } from '@/utils/labels'
import EmptyState from '@/components/EmptyState.vue'
import WorkFolderBar from '@/components/WorkFolderBar.vue'
import { FileText } from '@lucide/vue'
import ConfirmWorkbench from '@/components/ConfirmWorkbench.vue'
const props = defineProps<{ session: Session }>()
const emit = defineEmits<{ refresh: []; transcript: [file: SessionFile] }>()
const upload = ref<HTMLInputElement>(), busy = ref(false), error = ref(''), confirmOpen = ref(false), outboundOpen = ref(false), outbound = ref('')
const uploadProgress = ref<number | null>(null), localOpen = ref(false), findings = ref<LocalFinding[]>([])
const audioExts = ['mp3','m4a','wav','amr','aac','mp4']
const accept = computed(() => '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png' + (props.session.asr_enabled ? ',.mp3,.m4a,.wav,.amr,.aac,.mp4' : ''))
const certain = computed(() => findings.value.filter(f => f.check_code !== 'L-INFO'))
const unverified = computed(() => findings.value.length - certain.value.length)
const counts = computed(() => ['high','medium','low'].map(level => certain.value.filter(f => f.severity === level).length))
let localVersion = 0
watch(() => [props.session.id, props.session.status, props.session.files.map(f => f.id).join(',')].join(':'), async () => {
  if (!['active','awaiting_confirm'].includes(props.session.status)) return
  const version = ++localVersion
  try { const result = await assistantApi.localFindings(props.session.id); if (version === localVersion) findings.value = result.items }
  catch (e) { if (version === localVersion) error.value = e instanceof ApiError ? e.message : '本地校验加载失败，请刷新页面' }
}, { immediate: true })
const now = ref(Date.now()), parsingStart = ref(Date.now())
watch(() => props.session.status, status => {
  if (status === 'parsing') {
    const started = new Date(props.session.last_activity_at).getTime()
    parsingStart.value = Number.isFinite(started) ? started : Date.now()
  }
}, { immediate: true })
const timer = setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(timer))
const elapsed = computed(() => Math.max(0, Math.floor((now.value - parsingStart.value) / 1000)))
const pending = computed(() => props.session.files.filter(file => !file.confirmed_at))
const canUpload = computed(() => props.session.status === 'active' && !props.session.generating && !props.session.original_deleted && props.session.files.length < 10)
const canStance = computed(() => props.session.status === 'active' && !props.session.files.some(file => file.confirmed_at) && !props.session.original_deleted)
const task = computed(() => confirmationTask(props.session))
async function perform(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await action(); emit('refresh') }
  catch (e) { error.value = e instanceof ApiError ? e.message : '处理失败，请重试' }
  finally { busy.value = false }
}
async function addList(files: File[]) {
  if (files.length > 5 || files.length + props.session.files.length > 10) { error.value = '每次最多加入 5 个文件，会话合计最多 10 个文件'; return }
  for (const file of files) {
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    if (!accept.value.split(',').includes('.' + ext)) { error.value = '不支持该文件格式'; return }
    if (audioExts.includes(ext) && !props.session.asr_enabled) { error.value = '录音转写未启用，请联系管理员'; return }
    if (file.size > (audioExts.includes(ext) ? 500 : 30) * 1024 * 1024) { error.value = audioExts.includes(ext) ? '录音单个不能超过 500 MB' : '文档单个不能超过 30 MB'; return }
  }
  if (files.filter(f => audioExts.includes(f.name.split('.').pop()?.toLowerCase() ?? '')).length > 1) { error.value = '一次最多加入 1 个录音'; return }
  if (files.length && canUpload.value) {
    uploadProgress.value = 0
    try { await perform(() => assistantApi.addFiles(props.session.id, files, (loaded,total) => { uploadProgress.value = Math.round(loaded / total * 100) })) }
    finally { uploadProgress.value = null }
  }
}
async function added(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''
  await addList(files)
}
async function remove(file: SessionFile) {
  try { await ElMessageBox.confirm(`将移除“${file.original_name}”及其解析与脱敏数据，无法恢复。`, '移除文件', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' }) }
  catch { return }
  await perform(() => assistantApi.removeFile(props.session.id, file.id))
}
async function stance(value: Stance) { await perform(() => assistantApi.patch(props.session.id, { stance: value })) }
async function showOutbound() {
  await perform(async () => { const data = await assistantApi.outbound(props.session.id); outbound.value = data.text; outboundOpen.value = true })
}
const loadLocal = () => assistantApi.localFindings(props.session.id)
async function retryMedia(file: SessionFile) { await perform(() => assistantApi.retranscribe(props.session.id, file.id)) }
async function downloadTranscript(file: SessionFile) { await perform(async () => { const data = await assistantApi.transcript(props.session.id, file.id); await saveBlob(data.blob, data.filename) }) }
function confirmed() { confirmOpen.value = false; emit('refresh') }
</script>
<template>
  <aside class="files" @dragover.prevent @drop.prevent="canUpload && addList(Array.from($event.dataTransfer?.files ?? []))">
    <div class="file-heading"><h2>文件（{{ session.files.length }}）</h2><el-button size="small" :disabled="!canUpload" :loading="busy" @click="upload?.click()">加入文件</el-button><input ref="upload" class="file-input" type="file" multiple :accept="accept" @change="added"></div>
    <div v-if="uploadProgress !== null" role="status"><el-progress :percentage="uploadProgress" /><p class="hint">正在上传 {{ uploadProgress }}%</p></div>
    <WorkFolderBar :disabled="!canUpload" @add="addList" />
    <p v-if="!session.asr_enabled && !session.files.some(f => f.media)" class="hint">录音转写未启用</p>
    <p v-if="!canUpload" class="hint">{{ session.original_deleted ? '原件已按保留期限删除，本会话只能查看' : session.status !== 'active' ? '处理完成后可以继续加入文件' : session.generating ? '回复完成后可以继续加入文件' : '每会话最多 10 个文件' }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="session.status === 'parsing'"><h3>正在本机解析文件</h3><el-progress :percentage="100" :indeterminate="true" :show-text="false" /><p class="hint num">已用 {{ elapsed }} 秒</p><p class="hint">在本机读取正文并识别敏感信息，可以离开页面稍后继续。</p></template>
    <el-alert v-if="session.error_message" :title="session.error_message" type="error" :closable="false" />
    <div v-for="file in session.files" :key="file.id" class="file-row">
      <span class="file-badge">{{ file.media ? '音频' : file.ext.toUpperCase() }}</span><div class="file-info"><div class="file-name">{{ file.original_name }}</div><MediaRow v-if="file.media" :file="file" :busy="busy" @transcript="emit('transcript', file)" @retry="retryMedia(file)" @download="downloadTranscript(file)" /><el-tag v-else size="small" :type="statusTag(file.confirmed_at ? 'confirmed' : session.status).type" :effect="statusTag(file.confirmed_at ? 'confirmed' : session.status).effect">{{ file.confirmed_at ? '已确认' : assistantStatusLabel(session.status) }}</el-tag><p v-for="warning in file.warnings" :key="warning" class="warning">{{ warning }}</p></div>
      <el-button v-if="!file.confirmed_at && session.status !== 'parsing'" link type="danger" :disabled="busy" @click="remove(file)">移除</el-button>
    </div>
    <EmptyState v-if="!session.files.length" compact :icon="FileText" title="还没有文件" description="加入文件后，在本机解析与脱敏。" />
    <el-button v-if="session.status === 'awaiting_confirm'" type="primary" @click="confirmOpen = true">确认脱敏（{{ pending.length }} 个文件）</el-button>
    <div v-if="findings.length" class="session-settings"><ZoneHeader kind="local" :count="certain.length" /><p>高 {{ counts[0] }} · 中 {{ counts[1] }} · 低 {{ counts[2] }}</p><p v-if="unverified" class="hint">无法核验 {{ unverified }} 项</p><el-button link @click="localOpen = true">查看</el-button><p class="hint">发给模型的只有不含金额的摘要</p></div>
    <div class="session-settings"><h3>会话设置</h3><label>我方立场</label><el-select :model-value="session.stance" :disabled="!canStance || busy" @change="stance"><el-option label="销售" value="sales" /><el-option label="采购" value="purchase" /><el-option label="其他" value="other" /></el-select><p class="hint">第一个文件确认后不能修改立场</p><p>本会话代号 <span class="num">{{ session.code_count }}</span> 个</p><el-button :disabled="busy || session.status === 'parsing'" @click="showOutbound">查看外发文本</el-button><p class="hint">发给模型的只有脱敏后的文字，回复在本机还原</p></div>
    <el-drawer v-model="localOpen" title="本地校验" size="560px" destroy-on-close><LocalCheckList :loader="loadLocal" :load-key="session.id" readonly-locations /></el-drawer>
    <el-dialog v-model="confirmOpen" title="确认脱敏" fullscreen destroy-on-close :close-on-click-modal="false"><div class="confirmation"><ConfirmWorkbench :task="task" mode="assistant" @confirmed="confirmed" @stale="emit('refresh')" /></div></el-dialog>
    <el-dialog v-model="outboundOpen" title="查看外发文本" width="960px"><pre>{{ outbound }}</pre></el-dialog>
  </aside>
</template>
<style scoped>
.files { padding: 20px 16px; overflow-y: auto; min-height: 0; }
.file-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
h2 { font: var(--si-font-h2); margin: 0; }
h3 { font: var(--si-font-h3); }
.file-input { display: none; }
.file-row { display: flex; align-items: flex-start; gap: 8px; padding: 16px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.file-info { flex: 1; min-width: 0; }
.file-name { overflow-wrap: anywhere; font: var(--si-font-body); }
.file-badge { flex: none; border-radius: var(--el-border-radius-base); background: var(--el-fill-color-light); padding: 4px 8px; font: var(--si-font-caption); }
.warning { color: var(--el-color-warning); font: var(--si-font-caption); }
.session-settings { margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--el-border-color-light); }
.session-settings label { display: block; margin-bottom: 8px; }
.confirmation { height: calc(100vh - 110px); max-width: 1600px; margin: auto; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 65vh; overflow-y: auto; font: var(--si-font-body); }
</style>
