<script setup lang="ts">
// 智能助手输入框（V31-U5 第二节）：附件、模板与发送都在这里；首页时 session 为空，第一次动作交给页面建会话
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ArrowUp, FileAudio, FileText, Paperclip, Square, X } from '@lucide/vue'
import { assistantApi, type Session, type SessionFile, type Template, type MessageInput } from '@/api/assistant'
import type { ChatPreview, ChatDecision } from '@/api/chat'
import type { UserCategory } from '@/api/redaction'
import { ApiError } from '@/api/http'
import { ASSISTANT_TEMPLATE, USER_CATEGORY_OPTIONS, toOptions } from '@/utils/labels'
import { TEMPLATE_LIMITS, usableFiles } from '@/utils/assistantTemplates'
import TemplatePicker from './TemplatePicker.vue'

export interface ComposerStart { text: string; template: Template | null; files: File[]; modelId?: number }
const props = defineProps<{
  session: Session | null; template: Template | null; generating: number | null
  asrEnabled: boolean; showTemplates?: boolean; uploading?: number | null
  initial?: { text: string; autoSend: boolean; modelId?: number } | null
}>()
const emit = defineEmits<{
  'update:template': [value: Template | null]; sent: [text: string]; stop: [mid: number]
  start: [payload: ComposerStart]; files: [files: File[]]; open: [file: SessionFile]; remove: [file: SessionFile]; confirm: []
}>()

const init = props.initial
// ---- 模型 ----
const modelItems = ref<AvailableModel[]>([]), modelId = ref<number>(), modelError = ref(''), modelLoading = ref(false)
const allowedModels = computed(() => modelItems.value.filter(m => props.template !== 'compare' || m.structured_ok))
function selectAvailable(preferred?: number | null) {
  modelId.value = allowedModels.value.find(m => m.id === preferred)?.id
    ?? allowedModels.value.find(m => m.is_default)?.id ?? allowedModels.value[0]?.id
}
async function loadModels(preferred?: number | null) {
  modelLoading.value = !modelItems.value.length; modelError.value = ''
  try { modelItems.value = (await modelsApi.list('assist')).items; if (!allowedModels.value.some(m => m.id === modelId.value)) selectAvailable(preferred) }
  catch (e) { modelError.value = e instanceof ApiError ? e.message : '模型列表加载失败，请刷新页面' }
  finally { modelLoading.value = false }
}
onMounted(async () => {
  let preferred: number | null | undefined
  if (props.session) { try { preferred = [...(await assistantApi.messages(props.session.id)).items].reverse().find(m => m.role === 'assistant' && m.model_id)?.model_id } catch { /* 用默认模型 */ } }
  // 首页选的模型带到新会话的第一条消息，不被默认模型替换
  await loadModels(init?.modelId ?? preferred)
  if (init) { text.value = init.text; if (init.autoSend) { await nextTick(); void submit() } }
})
watch(() => props.template, template => {
  if (modelLoading.value || !modelItems.value.length || allowedModels.value.some(m => m.id === modelId.value)) return
  selectAvailable()
  if (template === 'compare') ElMessage.info(modelId.value ? '比价需要结构化输出，已切换可用模型' : '没有支持结构化输出的助手模型，请联系管理员')
})

// ---- 附件 ----
const picker = ref<HTMLInputElement>(), pickAccept = ref('')
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png', AUDIO = '.mp3,.m4a,.wav,.amr,.aac,.mp4'
const accept = computed(() => pickAccept.value || DOC + (props.asrEnabled ? ',' + AUDIO : ''))
function pick(kind: 'doc' | 'audio' | 'any' = 'any') { pickAccept.value = kind === 'audio' ? AUDIO : kind === 'doc' ? DOC : ''; void nextTick(() => picker.value?.click()) }
function picked(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; pickAccept.value = ''
  if (files.length) attach(files)
}
function attach(files: File[]) { if (props.session) emit('files', files); else emit('start', { text: text.value, template: props.template, files, modelId: modelId.value }) }
const files = computed(() => props.session ? usableFiles(props.session, props.template) : [])
function chip(file: SessionFile) {
  const m = file.media
  if (m) {
    const label = { decoding: '解码中', queued: '排队转写', waiting_memory: '等待内存', transcribing: `转写 ${m.duration_ms ? Math.min(99, Math.floor(m.progress / m.duration_ms * 100)) : 0}%`, done: file.confirmed_at ? '已确认' : '请校对', failed: '转写失败' }[m.stage]
    return { label, tone: m.stage === 'failed' ? 'danger' : file.confirmed_at ? 'ok' : m.stage === 'done' ? 'warn' : 'busy', openable: m.stage === 'done' }
  }
  if (file.confirmed_at) return { label: '已确认', tone: 'ok', openable: false }
  if (props.session?.status === 'parsing') return { label: '解析中', tone: 'busy', openable: false }
  if (props.session?.status === 'parse_failed') return { label: '解析失败', tone: 'danger', openable: false }
  return { label: '待确认', tone: 'warn', openable: true }
}

// ---- 模板 ----
const target = ref('英文'), focus = ref('全部'), tone = ref('正式'), language = ref('同来函'), fileIds = ref<string[]>([])
watch(() => files.value.map(f => f.id).join(','), () => { fileIds.value = files.value.map(f => f.id) }, { immediate: true })
function chooseTemplate(value: Template | null) {
  emit('update:template', value)
  // 会话里还没有这个模板能用的文件时，直接打开选择框（会议纪要只收录音）
  if (value && TEMPLATE_LIMITS[value][0] > 0 && !(props.session?.files.length)) pick(value === 'minutes' ? 'audio' : 'doc')
}
const limits = computed(() => props.template ? TEMPLATE_LIMITS[props.template] : [0, 10] as [number, number])

// ---- 发送：待确认时自动引导确认，处理中时等完成，消息无疑似项时直接发送 ----
const text = ref(''), useKb = ref(false), useHistory = ref(false), busy = ref(false), sending = ref(false), error = ref(''), open = ref(false)
const resume = ref(false)
watch(() => props.session?.kb_enabled, value => { useKb.value = !!value }, { immediate: true })
const preview = ref<ChatPreview | null>(null)
const decisions = ref<{ decision: '' | 'replace' | 'keep'; category: UserCategory | '' }[]>([])
const confirmed = computed(() => decisions.value.every(d => d.decision === 'keep' || (d.decision === 'replace' && d.category)))
const processing = computed(() => !!props.session && (props.session.status === 'parsing' || props.session.status === 'awaiting_confirm'))
const reason = computed(() => {
  if (modelLoading.value) return '正在加载模型列表'
  if (modelError.value) return modelError.value
  if (!modelId.value) return '没有可用的助手模型，请联系管理员'
  if (props.session?.original_deleted) return '原件已按保留期限删除，本会话只能查看'
  if (props.session && !processing.value && !props.session.can_send) return props.session.send_reason ?? '当前不能发送'
  // 文件还在处理时不按数量拦：确认完成后会自动带上
  if (props.template && !processing.value && props.session && (fileIds.value.length < limits.value[0] || fileIds.value.length > limits.value[1]))
    return props.template === 'minutes' ? '请加入 1～3 个录音' : limits.value[0] === limits.value[1] ? `请加入 ${limits.value[0]} 个文件` : `请加入 ${limits.value[0]}～${limits.value[1]} 个文件`
  if (!text.value.trim() && (!props.template || props.template === 'reply' && !fileIds.value.length)) return '请输入问题或粘贴来函'
  return ''
})
const waitingText = computed(() => !resume.value || !props.session ? '' : props.session.status === 'parsing' ? '文件处理完成后自动发送' : props.session.status === 'awaiting_confirm' ? '确认脱敏后自动发送' : '')
function input(): MessageInput {
  const params: Record<string, unknown> = props.template === 'translate' ? { file_id: fileIds.value[0], target: target.value } : { file_ids: [...fileIds.value] }
  if (props.template === 'summary') params.focus = focus.value
  if (props.template === 'reply') Object.assign(params, { tone: tone.value, language: language.value })
  return { model_id: modelId.value, message: text.value, template: props.template, params, use_kb: useKb.value && !!props.session?.kb_enabled, use_history: useHistory.value }
}
let pending: MessageInput | null = null
async function submit() {
  if (busy.value || sending.value || props.generating !== null) return
  if (!props.session) { if (!reason.value || text.value.trim() || props.template) emit('start', { text: text.value, template: props.template, files: [], modelId: modelId.value }); return }
  if (processing.value) {
    resume.value = true
    if (props.session.status === 'awaiting_confirm') emit('confirm')
    return
  }
  if (reason.value) return
  busy.value = true; error.value = ''; pending = input()
  try {
    preview.value = await assistantApi.preview(props.session.id, pending)
    decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' }))
    if (preview.value.suspects.length) open.value = true
    else await send()
  } catch (e) { error.value = e instanceof ApiError ? e.message : '发送失败，请重试' }
  finally { busy.value = false }
}
// 处理完成：待确认时打开确认，可发送时继续发送
watch(() => [props.session?.status, props.session?.can_send] as const, ([status]) => {
  if (!resume.value || !props.session) return
  if (status === 'awaiting_confirm') emit('confirm')
  else if (status === 'active') { resume.value = false; void nextTick(submit) }
  else if (status === 'parse_failed') resume.value = false
})
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault(); void submit()
}
async function send() {
  if (!pending || !confirmed.value || sending.value || !props.session) return
  sending.value = true; error.value = ''
  const selected: ChatDecision[] = (preview.value?.suspects ?? []).map((suspect, index) => ({
    start: suspect.start, end: suspect.end, decision: decisions.value[index]!.decision as 'replace' | 'keep',
    ...(decisions.value[index]!.category ? { category: decisions.value[index]!.category as UserCategory } : {}),
  }))
  try { await assistantApi.send(props.session.id, { ...pending, decisions: selected }); open.value = false; const sentText = text.value; text.value = ''; pending = null; emit('sent', sentText) }
  catch (e) {
    error.value = e instanceof ApiError ? e.message : '发送失败，请重试'
    if (e instanceof ApiError && e.code === 'CHAT_SUSPECTS_PENDING' && pending) {
      try { preview.value = await assistantApi.preview(props.session.id, pending); decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' })); open.value = true }
      catch (freshError) { error.value = freshError instanceof ApiError ? freshError.message : '预览更新失败，请取消后重试' }
    }
  }
  finally { sending.value = false }
}
const dragging = ref(false)
function drop(event: DragEvent) { dragging.value = false; const list = Array.from(event.dataTransfer?.files ?? []); if (list.length) attach(list) }
defineExpose({ pick, attach })
</script>

<template>
  <div class="composer">
    <p v-if="session?.original_deleted" class="hint">原件已按保留期限删除，本会话只能查看</p>
    <template v-else>
      <div class="box" :class="{ dragging }" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
        <div v-if="session?.files.length || uploading !== null && uploading !== undefined" class="chips">
          <span v-for="file in session?.files ?? []" :key="file.id" class="chip" :class="`chip--${chip(file).tone}`">
            <button type="button" class="chip-main" :disabled="!chip(file).openable && !(file.media && file.confirmed_at)" :title="file.media?.error ? '转写失败，可在文件抽屉中重试' : undefined" @click="emit('open', file)">
              <component :is="file.media ? FileAudio : FileText" :size="14" :stroke-width="1.75" /><span class="chip-name">{{ file.original_name }}</span><span class="chip-state">{{ chip(file).label }}</span>
            </button>
            <button v-if="!file.confirmed_at && session?.status !== 'parsing'" type="button" class="chip-x" :aria-label="`移除 ${file.original_name}`" @click="emit('remove', file)"><X :size="12" :stroke-width="2" /></button>
          </span>
          <span v-if="uploading !== null && uploading !== undefined" class="chip chip--busy"><span class="chip-main">正在上传 {{ uploading }}%</span></span>
        </div>
        <div v-if="template" class="template-row">
          <span class="template-tag">{{ ASSISTANT_TEMPLATE[template] }}<button type="button" class="chip-x" aria-label="取消模板" @click="emit('update:template', null)"><X :size="12" :stroke-width="2" /></button></span>
          <el-select v-if="template === 'translate'" v-model="target" size="small" class="param" aria-label="目标语言"><el-option v-for="option in ['中文','英文','日文','韩文','俄文','西班牙文','法文','德文','阿拉伯文']" :key="option" :label="`译成${option}`" :value="option" /></el-select>
          <el-select v-if="template === 'summary'" v-model="focus" size="small" class="param" aria-label="侧重点"><el-option v-for="option in ['全部','义务与期限','金额与付款','风险']" :key="option" :label="`侧重：${option}`" :value="option" /></el-select>
          <el-select v-if="template === 'reply'" v-model="tone" size="small" class="param" aria-label="回复语气"><el-option v-for="option in ['正式','简洁','友好坚定']" :key="option" :label="option" :value="option" /></el-select>
          <el-select v-if="template === 'reply'" v-model="language" size="small" class="param" aria-label="回复语言"><el-option v-for="option in ['中文','英文','同来函']" :key="option" :label="option" :value="option" /></el-select>
          <span v-if="template === 'translate'" class="hint where">云端 · 脱敏后发送</span>
        </div>
        <el-input v-model="text" type="textarea" :autosize="{ minRows: session ? 1 : 3, maxRows: 8 }" maxlength="4000" :show-word-limit="text.length > 3500" :placeholder="session ? '继续提问…' : '提问，或把合同、报价单、会议录音拖到这里'" aria-label="问题或来函" @keydown="onKeydown" />
        <div class="bar">
          <el-tooltip content="加入文件或录音" placement="top"><el-button text circle aria-label="加入文件或录音" :disabled="!!session && (session.files.length >= 10 || !!session.generating)" @click="pick()"><Paperclip :size="16" :stroke-width="1.75" /></el-button></el-tooltip>
          <input ref="picker" class="file-input" type="file" multiple :accept="accept" @change="picked">
          <el-select v-model="modelId" class="model-select" size="small" aria-label="助手模型" placeholder="选择模型" :loading="modelLoading" :disabled="sending || busy || generating !== null" @visible-change="$event && loadModels(modelId)"><el-option v-for="model in allowedModels" :key="model.id" :value="model.id" :label="model.display_name" /></el-select>
          <el-select v-if="files.length" v-model="fileIds" multiple collapse-tags size="small" class="files-select" placeholder="不使用文件" aria-label="使用的文件"><el-option v-for="file in files" :key="file.id" :label="`文件 ${file.seq}：${file.original_name}`" :value="file.id" /></el-select>
          <el-checkbox v-if="session?.kb_enabled" v-model="useKb" size="small">知识库</el-checkbox><el-checkbox v-model="useHistory" size="small">参考历史合同</el-checkbox>
          <el-button v-if="generating !== null" class="send" circle aria-label="停止生成" title="停止生成" @click="emit('stop', generating)"><Square :size="14" :stroke-width="2" fill="currentColor" /></el-button>
          <el-button v-else class="send" type="primary" circle aria-label="发送" :title="reason || '发送（Enter）'" :disabled="!!reason && !processing" :loading="busy || sending" @click="submit"><ArrowUp v-if="!busy && !sending" :size="16" :stroke-width="2" /></el-button>
        </div>
      </div>
      <p v-if="waitingText" class="hint status" role="status">{{ waitingText }} · <el-button link size="small" @click="resume = false">取消</el-button></p>
      <p v-else-if="reason && (text.trim() || template)" class="hint status">{{ reason }}</p>
      <el-alert v-if="error && !open" :title="error" type="error" :closable="false" />
      <TemplatePicker v-if="showTemplates" class="quick" chips :asr-enabled="asrEnabled" :model-value="template" @update:model-value="chooseTemplate" />
    </template>
    <el-dialog v-model="open" title="确认发送内容" width="640px" :close-on-click-modal="false" :close-on-press-escape="!sending" :show-close="!sending" append-to-body>
      <p class="hint">消息中有疑似敏感信息，请逐项决定是否替换为代号。发给模型的只有脱敏后的文字。</p><pre>{{ preview?.redacted }}</pre>
      <div v-for="(suspect, index) in preview?.suspects" :key="index" class="suspect">
        <p>{{ suspect.text }}</p><el-radio-group v-model="decisions[index]!.decision"><el-radio value="replace">替换</el-radio><el-radio value="keep">不替换</el-radio></el-radio-group>
        <el-select v-if="decisions[index]!.decision === 'replace'" v-model="decisions[index]!.category" aria-label="替换类别"><el-option v-for="option in toOptions(USER_CATEGORY_OPTIONS)" :key="option.value" :value="option.value" :label="option.label" /></el-select>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template #footer><el-button :disabled="sending" @click="open = false">取消</el-button><el-button type="primary" :disabled="!confirmed" :loading="sending" @click="send">确认发送</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.composer { padding: 12px 24px 16px; }
.composer > * { max-width: 760px; margin-left: auto; margin-right: auto; }
.box { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px 8px; border: 1px solid var(--si-hair-strong); border-radius: var(--si-radius-card); background: var(--si-surface-1); transition: border-color var(--si-dur-1) var(--si-ease); }
.box:focus-within { border-color: var(--si-blue); }
.box.dragging { border-color: var(--si-blue); border-style: dashed; background: color-mix(in srgb, var(--si-blue) 6%, var(--si-surface-1)); }
.box :deep(.el-textarea__inner) { padding: 0; border: 0; box-shadow: none; background: transparent; font: var(--si-font-body); resize: none; }
.chips, .template-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { display: inline-flex; align-items: center; max-width: 100%; border-radius: var(--el-border-radius-base); background: var(--si-surface-2); font: var(--si-font-caption); }
.chip-main { display: inline-flex; align-items: center; gap: 6px; min-width: 0; padding: 4px 8px; border: 0; background: none; color: inherit; font: inherit; cursor: pointer; }
.chip-main:disabled { cursor: default; }
.chip-name { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--si-ink-1); }
.chip-state { color: var(--si-ink-3); }
.chip--ok .chip-state { color: var(--si-ok-ink); }
.chip--warn .chip-state { color: var(--si-warn-ink); font-weight: 600; }
.chip--danger .chip-state { color: var(--si-danger-ink); }
.chip--busy .chip-state, .chip--busy .chip-main { color: var(--si-blue-ink); }
.chip-x { display: grid; place-items: center; width: 20px; height: 20px; margin-right: 4px; padding: 0; border: 0; border-radius: 4px; background: none; color: var(--si-ink-3); cursor: pointer; }
.chip-x:hover { background: var(--si-surface-1); color: var(--si-ink-1); }
.template-tag { display: inline-flex; align-items: center; gap: 2px; padding: 2px 0 2px 8px; border-radius: var(--el-border-radius-base); background: color-mix(in srgb, var(--si-blue) 14%, transparent); color: var(--si-blue-ink); font: var(--si-font-label); }
.param { width: 132px; }
.where { margin: 0; }
.bar { display: flex; align-items: center; gap: 8px; }
.bar :deep(.el-checkbox) { margin-right: 0; }
.model-select { width: 150px; }
.files-select { width: 180px; }
.send { margin-left: auto; width: 32px; height: 32px; }
.file-input { display: none; }
.status { margin: 6px 0 0; }
.quick { margin-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-body); max-height: 320px; overflow-y: auto; padding: 16px; background: var(--el-fill-color-light); border-radius: var(--el-border-radius-base); }
.suspect { border-top: 1px solid var(--el-border-color-light); padding: 12px 0; }
.suspect :deep(.el-select) { width: 160px; }
</style>
