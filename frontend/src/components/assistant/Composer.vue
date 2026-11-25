<script setup lang="ts">
// 智能助手输入框（V31-U5 第二节）：附件、模板与发送都在这里；首页时 session 为空，第一次动作交给页面建会话
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ArrowUp, Check, ChevronDown, ChevronRight, FileAudio, FileImage, FileSpreadsheet, FileText, Loader2, Lock, Paperclip, Sparkle, Square, X } from '@lucide/vue'
import { pastedFiles } from '@/composables/usePasteFiles'
import { assistantApi, type AssistantPreview, type Session, type SessionFile, type Template, type MessageInput } from '@/api/assistant'
import type { ChatDecision } from '@/api/chat'
import type { UserCategory } from '@/api/redaction'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { useDraftsStore } from '@/stores/drafts'
import { ASSISTANT_TEMPLATE, USER_CATEGORY_OPTIONS, toOptions } from '@/utils/labels'
import { TEMPLATE_LIMITS, usableFiles } from '@/utils/assistantTemplates'

export interface ComposerStart { action: 'attach' | 'send'; text: string; template: Template | null; files: File[]; modelId?: number }
const props = withDefaults(defineProps<{
  session: Session | null; template: Template | null; generating: number | null
  asrEnabled: boolean; showTemplates?: boolean; uploading?: number | null
  /** 首页大号输入框（澄明 8.2）：圆角 24、完整档毛玻璃；否则为对话页底部的输入框 */
  hero?: boolean
  initial?: { text: string; submitted: boolean; modelId?: number } | null
  active?: boolean
  confirmedTick?: number
  /** 讨论锚点：设置后消息带 finding_id，只围绕该问题讨论（V32-B-02） */
  anchor?: { id: string } | null
}>(), { active: true })
const emit = defineEmits<{
  'update:template': [value: Template | null]; sent: [text: string]; stop: [mid: number]
  start: [payload: ComposerStart]; waiting: [value: boolean]; busy: []; files: [files: File[]]; open: [file: SessionFile]; remove: [file: SessionFile]; confirm: []; retry: [file: SessionFile]
}>()

const init = props.initial
const auth = useAuthStore()
// 只有“文件审核”权限的人不能直接提问，只能在审核问题上讨论（V32-B-01 4）
const canAsk = computed(() => auth.can('use.assistant'))
const canSend = computed(() => canAsk.value || !!props.anchor)
// ---- 模型 ----
const modelItems = ref<AvailableModel[]>([]), modelId = ref<number>(), modelError = ref(''), modelLoading = ref(false)
const allowedModels = computed(() => modelItems.value.filter(m => props.template !== 'compare' || m.structured_ok))
function selectAvailable(preferred?: number | null) {
  modelId.value = allowedModels.value.find(m => m.id === preferred)?.id
    ?? allowedModels.value.find(m => m.is_default)?.id ?? allowedModels.value[0]?.id
}
async function loadModels(preferred?: number | null) {
  if (!canSend.value) return
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
  if (init?.submitted && !destroyed) { await nextTick(); void submit() }
})
watch(() => !!props.anchor, anchored => { if (anchored && !modelItems.value.length) void loadModels() })
watch(() => props.template, template => {
  if (modelLoading.value || !modelItems.value.length || allowedModels.value.some(m => m.id === modelId.value)) return
  selectAvailable()
  if (template === 'compare') ElMessage.info(modelId.value ? '比价需要结构化输出，已切换可用模型' : '没有支持结构化输出的助手模型，请联系管理员')
})

// ---- 附件 ----
const picker = ref<HTMLInputElement>(), pickAccept = ref('')
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png', AUDIO = '.mp3,.m4a,.wav,.amr,.aac,.mp4'
// 智能助手只收文档；会议录音在“会议纪要”菜单
const accept = computed(() => pickAccept.value || DOC)
function pick(kind: 'doc' | 'audio' | 'any' = 'any') { pickAccept.value = kind === 'audio' ? AUDIO : kind === 'doc' ? DOC : ''; void nextTick(() => picker.value?.click()) }
function picked(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; pickAccept.value = ''
  if (files.length) attach(files)
}
function attach(files: File[]) { if (props.session) emit('files', files); else emit('start', { action: 'attach', text: text.value, template: props.template, files, modelId: modelId.value }) }
const files = computed(() => props.session ? usableFiles(props.session, props.template) : [])
function chip(file: SessionFile) {
  const m = file.media
  if (m) {
    const label = { decoding: '解码中', queued: '排队转写', waiting_memory: '等待内存', transcribing: `转写 ${m.duration_ms ? Math.min(99, Math.floor(m.progress / m.duration_ms * 100)) : 0}%`, done: file.confirmed_at ? '已确认' : '请校对', failed: '转写失败' }[m.stage]
    return { label, tone: m.stage === 'failed' ? 'danger' : file.confirmed_at ? 'ok' : m.stage === 'done' ? 'warn' : 'busy', openable: m.stage === 'done' }
  }
  if (file.parse_state === 'failed') return { label: '解析失败', tone: 'danger', openable: false }
  if (file.parse_state === 'parsing') return { label: '本机解析中', tone: 'busy', openable: false }
  if (file.confirmed_at) return { label: '已确认', tone: 'ok', openable: false }
  return { label: '待确认', tone: 'warn', openable: true }
}
// V31-U5F-04：已确认、本模板可用的文件，点标签切换“本次提问是否使用”（默认全选）；待确认的点标签去确认
const usable = computed(() => new Set(files.value.map(f => f.id)))
function selectable(file: SessionFile) { return !!file.confirmed_at && usable.value.has(file.id) }
function clickChip(file: SessionFile) {
  if (selectable(file)) {
    fileIds.value = fileIds.value.includes(file.id) ? fileIds.value.filter(id => id !== file.id) : [...fileIds.value, file.id]
  } else emit('open', file)
}
// 文件标签（澄明 8.2）：类型色图标、名称、状态；读取中带细进度条，等你确认为琥珀色、点了去确认
function fileKind(file: SessionFile) {
  if (file.media) return 'aud'
  const ext = file.ext.toLowerCase()
  return ext === 'pdf' ? 'pdf' : ['xlsx', 'xls', 'et'].includes(ext) ? 'xls' : ['jpg', 'jpeg', 'png'].includes(ext) ? 'img' : 'doc'
}
const KIND_ICON = { aud: FileAudio, pdf: FileText, xls: FileSpreadsheet, img: FileImage, doc: FileText } as const
function tag(file: SessionFile): { text: string; tone: string; progress: number | null } {
  if (file.media) { const c = chip(file); return { text: c.label ?? '', tone: c.tone, progress: null } }
  if (file.parse_state === 'failed') return { text: '读取失败', tone: 'danger', progress: null }
  if (file.parse_state === 'parsing' || props.session?.status === 'parsing' && !file.confirmed_at) {
    const p = props.session?.parse_progress?.files?.[file.id]
    if (p?.total) return { text: `正在本机读取 · 第 ${Math.min(p.done ?? 0, p.total)} / ${p.total} 页`, tone: 'busy', progress: Math.min(1, (p.done ?? 0) / p.total) }
    return { text: props.session?.parse_progress?.phase === 'detect' ? '正在本机遮住名称与金额' : '正在本机读取', tone: 'busy', progress: null }
  }
  if (!file.confirmed_at) {
    // 遮住数是整个会话的合计：只有一份文件待确认时才写数字
    const single = (props.session?.files ?? []).filter(f => !f.confirmed_at && !f.media).length === 1
    return { text: single && props.session?.code_count ? `已遮住 ${props.session.code_count} 处 · 等你确认` : '等你确认 · 点此查看', tone: 'warn', progress: null }
  }
  if (selectable(file)) return { text: fileIds.value.includes(file.id) ? `${file.page_count ? `${file.page_count} 页 · ` : ''}本次提问使用` : '本次不用 · 点击使用', tone: 'ok', progress: null }
  return { text: '已确认', tone: 'ok', progress: null }
}
const failures = computed(() => (props.session?.files ?? []).filter(f => f.parse_error || f.media?.stage === 'failed'))
const removable = (_file: SessionFile) => props.session?.status !== 'parsing' && !props.session?.generating && !props.session?.review_running

// ---- 模板 ----
const target = ref('英文'), focus = ref('全部'), tone = ref('正式'), language = ref('同来函'), fileIds = ref<string[]>([])
watch(() => files.value.map(f => f.id).join(','), () => { fileIds.value = files.value.map(f => f.id) }, { immediate: true })
const limits = computed(() => props.template ? TEMPLATE_LIMITS[props.template] : [0, 10] as [number, number])
const TEMPLATE_DESC: Partial<Record<Template, string>> = { translate: '分段翻译并核对原文', summary: '提取文件中的重点', reply: '根据来函起草回复', crosscheck: '在本机逐项核对多份单证是否一致', compare: '对齐报价项目，在本地比较价格', minutes: '把录音整理为议题、决议与待办' }
const need = computed(() => {
  if (!props.template) return ''
  const [min, max] = limits.value, unit = props.template === 'minutes' ? '个录音' : '个文件'
  return min === 0 ? `可附至多 ${max} ${unit}` : min === max ? `需要 ${min} ${unit}` : `需要 ${min}～${max} ${unit}`
})
// 文件不够时显示“加入文件”；文件还在处理时不显示（确认完成后会自动带上）
const short = computed(() => !!props.template && !processing.value && (props.session ? files.value.length : 0) < limits.value[0])
const canPick = computed(() => !props.session || (props.session.files.length < 10 && !props.session.generating && !props.session.review_running))

// ---- 发送：待确认时自动引导确认，处理中时等完成，消息无疑似项时直接发送 ----
// 草稿按会话（及讨论锚点）存内存，发送成功清空时随之清掉（V321-E-12）
const drafts = useDraftsStore()
const draftKey = props.session ? `${props.session.id}:${props.anchor?.id ?? ''}` : ''
const text = ref(init?.text ?? (draftKey ? drafts.get(draftKey) : ''))
if (draftKey) {
  if (init) drafts.set(draftKey, text.value)
  watch(text, value => drafts.set(draftKey, value), { flush: 'sync' })
}
const useKb = ref(false), useHistory = ref(false), busy = ref(false), sending = ref(false), error = ref(''), open = ref(false)
const resume = ref(false)
let destroyed = false
function cancelWaiting() {
  if (!resume.value) return
  resume.value = false
  emit('waiting', false)
}
function startWaiting() {
  resume.value = true
  emit('waiting', true)
}
function active() { return !destroyed && props.active !== false }
watch(() => props.active, value => { if (value === false) { cancelWaiting(); open.value = false } }, { flush: 'sync' })
watch([text, () => props.template, modelId, target, focus, tone, language, useKb, useHistory], cancelWaiting, { flush: 'sync' })
onBeforeUnmount(() => { destroyed = true; cancelWaiting() })
const omitAdvice = ref(false), blockedCode = ref('')
const questionInput = ref<{ focus: () => void }>()
watch(() => props.anchor?.id, () => { omitAdvice.value = false; blockedCode.value = '' })
function failDiscussion(e: unknown) {
  blockedCode.value = e instanceof ApiError ? e.code : ''
  if (blockedCode.value.startsWith('DISCUSSION_')) { open.value = false; void nextTick(() => questionInput.value?.focus()) }
}
function withoutAdvice() { omitAdvice.value = true; blockedCode.value = ''; void submit() }
watch(() => props.session?.kb_enabled, value => { useKb.value = !!value }, { immediate: true })
const preview = ref<AssistantPreview | null>(null)
const decisions = ref<{ decision: '' | 'replace' | 'keep'; category: UserCategory | '' }[]>([])
const confirmed = computed(() => decisions.value.every(d => d.decision === 'keep' || (d.decision === 'replace' && d.category)))
const processing = computed(() => !!props.session && (props.session.status === 'parsing' || props.session.status === 'awaiting_confirm'))
const reason = computed(() => {
  if (!canSend.value) return '你没有“智能助手”权限，只能在审核结果的问题上讨论'
  if (props.session?.review_running) return '审核进行中，完成后再发送'
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
const modelName = computed(() => modelItems.value.find(m => m.id === modelId.value)?.display_name ?? (modelLoading.value ? '加载模型…' : '选择模型'))
const modelLocked = computed(() => allowedModels.value.length <= 1 || sending.value || busy.value || props.generating !== null)
function onModelMenu(visible: boolean) { if (visible) void loadModels(modelId.value) }
function pickModel(id: number) { modelId.value = id }
const localOnly = computed(() => props.session?.policy === 'local_only')
const waitingText = computed(() => !resume.value || !props.session ? '' : props.session.status === 'parsing' ? '已提交提问，文件读取并确认后继续' : props.session.status === 'awaiting_confirm' ? '确认脱敏后继续已提交的提问' : '')
function input(): MessageInput {
  if (props.anchor) return { model_id: modelId.value, message: text.value, template: null, params: omitAdvice.value ? { omit_finding_advice: true } : {}, use_kb: useKb.value && !!props.session?.kb_enabled, use_history: useHistory.value, finding_id: props.anchor.id }
  const params: Record<string, unknown> = props.template === 'translate' ? { file_id: fileIds.value[0], target: target.value } : { file_ids: [...fileIds.value] }
  if (props.template === 'summary') params.focus = focus.value
  if (props.template === 'reply') Object.assign(params, { tone: tone.value, language: language.value })
  return { model_id: modelId.value, message: text.value, template: props.template, params, use_kb: useKb.value && !!props.session?.kb_enabled, use_history: useHistory.value }
}
let pending: MessageInput | null = null
async function submit() {
  if (!active() || busy.value || sending.value || props.generating !== null) return
  if (!props.session) {
    if (!reason.value || text.value.trim() || props.template) emit('start', { action: 'send', text: text.value, template: props.template, files: [], modelId: modelId.value })
    return
  }
  if (processing.value) {
    if (!text.value.trim() && !props.template) return
    startWaiting()
    if (props.session.status === 'awaiting_confirm') emit('confirm')
    return
  }
  if (reason.value) return
  busy.value = true; error.value = ''; pending = input()
  try {
    const prepared = await assistantApi.preview(props.session.id, pending)
    if (!active()) return
    preview.value = prepared
    pending.expected_material_version = preview.value.material_version
    decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' }))
    if (preview.value.suspects.length) open.value = true
    else await send()
  } catch (e) { failDiscussion(e); error.value = e instanceof ApiError ? e.message : '发送失败，请重试'; if (e instanceof ApiError && e.code === 'SESSION_BUSY') emit('busy') }
  finally { busy.value = false }
}
// 等待的是用户明确提交的提问。只有本页确认成功才能续发，切换或取消时保留输入。
watch(() => props.session?.status, status => {
  if (!resume.value || !active()) return
  if (status === 'awaiting_confirm') emit('confirm')
  else if (status === 'parse_failed') cancelWaiting()
  else if (status === 'active' && props.anchor) { cancelWaiting(); void submit() }
})
watch(() => props.confirmedTick, () => {
  if (!resume.value || !active() || props.session?.status !== 'active') return
  cancelWaiting()
  void submit()
})
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault(); void submit()
}
async function send() {
  if (!active() || !pending || !confirmed.value || sending.value || !props.session) return
  sending.value = true; error.value = ''
  const selected: ChatDecision[] = (preview.value?.suspects ?? []).map((suspect, index) => ({
    start: suspect.start, end: suspect.end, decision: decisions.value[index]!.decision as 'replace' | 'keep',
    ...(decisions.value[index]!.category ? { category: decisions.value[index]!.category as UserCategory } : {}),
  }))
  try { await assistantApi.send(props.session.id, { ...pending, decisions: selected }); open.value = false; const sentText = text.value; text.value = ''; pending = null; blockedCode.value = ''; omitAdvice.value = false; emit('sent', sentText) }
  catch (e) {
    failDiscussion(e)
    error.value = e instanceof ApiError ? e.message : '发送失败，请重试'
    if (e instanceof ApiError && e.code === 'SESSION_BUSY') { open.value = false; emit('busy') }
    // 材料有变化（V32-A-01）：重新取预览，让用户核对后再确认发送
    if (e instanceof ApiError && ['CHAT_SUSPECTS_PENDING', 'MATERIAL_CHANGED'].includes(e.code) && pending) {
      try { preview.value = await assistantApi.preview(props.session.id, pending); pending.expected_material_version = preview.value.material_version; decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' })); open.value = true }
      catch (freshError) { error.value = freshError instanceof ApiError ? freshError.message : '预览更新失败，请取消后重试' }
    }
  }
  finally { sending.value = false }
}
const dragging = ref(false)
function drop(event: DragEvent) { dragging.value = false; const list = Array.from(event.dataTransfer?.files ?? []); if (list.length) attach(list) }
// V31-U5F-04：输入框里 Ctrl+V 粘贴文件（资源管理器复制的文件、截图）与拖入相同；只有文字时照常粘贴
function paste(event: ClipboardEvent) { const list = pastedFiles(event); if (!list.length) return; event.preventDefault(); attach(list) }
defineExpose({ pick, attach, cancelWaiting })
</script>

<template>
  <div class="composer" :class="{ 'is-hero': hero, 'is-anchor': anchor }">
    <p v-if="session?.original_deleted" class="hint">原件已按保留期限删除，本会话只能查看</p>
    <template v-else>
      <div class="box" :class="{ dragging, 'si-glass': hero }" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop" @paste="paste">
        <div v-if="!anchor && (session?.files.length || uploading !== null && uploading !== undefined)" class="files">
          <div v-for="file in session?.files ?? []" :key="file.id" class="ft" :class="[`ft-${fileKind(file)}`, `ft--${tag(file).tone}`, { 'ft--off': selectable(file) && !fileIds.includes(file.id) }]">
            <button type="button" class="ft-main" :disabled="!selectable(file) && !chip(file).openable && !(file.media && file.confirmed_at)"
                    :aria-pressed="selectable(file) ? fileIds.includes(file.id) : undefined"
                    :title="file.parse_error ?? (file.media?.error ? '转写失败，可在文件面板中重试' : selectable(file) ? (fileIds.includes(file.id) ? '本次提问使用此文件，点击取消' : '本次提问不使用此文件，点击使用') : undefined)" @click="clickChip(file)">
              <span class="fi"><Check v-if="selectable(file) && fileIds.includes(file.id)" :size="16" :stroke-width="2" /><component :is="KIND_ICON[fileKind(file)]" v-else :size="16" :stroke-width="1.75" /></span>
              <span class="ft-text"><span class="nm">{{ file.original_name }}</span><span class="st"><Loader2 v-if="tag(file).tone === 'busy'" :size="12" :stroke-width="2" class="spin" aria-hidden="true" />{{ tag(file).text }}</span></span>
              <ChevronRight v-if="tag(file).tone === 'warn'" :size="16" :stroke-width="1.75" class="ft-go" aria-hidden="true" />
            </button>
            <button v-if="removable(file)" type="button" class="ft-x" :aria-label="`从会话移除 ${file.original_name}`" title="从会话移除" @click="emit('remove', file)"><X :size="14" :stroke-width="2" /></button>
            <span v-if="tag(file).tone === 'busy'" class="ft-bar" :class="{ 'si-progress-flow': tag(file).progress === null }"><i v-if="tag(file).progress !== null" :style="{ width: `${Math.max(4, (tag(file).progress ?? 0) * 100)}%` }" /></span>
          </div>
          <div v-if="uploading !== null && uploading !== undefined" class="ft ft-doc ft--busy">
            <span class="ft-main"><span class="fi"><Loader2 :size="16" :stroke-width="2" class="spin" /></span><span class="ft-text"><span class="nm">正在上传</span><span class="st num">{{ uploading }}%</span></span></span>
            <span class="ft-bar"><i :style="{ width: `${Math.max(4, uploading)}%` }" /></span>
          </div>
        </div>
        <p v-for="file in anchor ? [] : failures" :key="`f-${file.id}`" class="failure" role="alert">
          <span class="failure-text">{{ file.original_name }}：{{ file.parse_error ?? file.media?.error ?? '处理失败' }}</span>
          <el-button v-if="file.media" link size="small" type="primary" @click="emit('retry', file)">重试</el-button>
          <el-button v-else-if="removable(file)" link size="small" type="danger" @click="emit('remove', file)">移除</el-button>
        </p>
        <div v-if="template" class="template-row">
          <span class="template-tag">{{ ASSISTANT_TEMPLATE[template] }}<button type="button" class="ft-x" aria-label="取消模板" @click="emit('update:template', null)"><X :size="12" :stroke-width="2" /></button></span>
          <el-select v-if="template === 'translate'" v-model="target" size="small" class="param" aria-label="目标语言"><el-option v-for="option in ['中文','英文','日文','韩文','俄文','西班牙文','法文','德文','阿拉伯文']" :key="option" :label="`译成${option}`" :value="option" /></el-select>
          <el-select v-if="template === 'summary'" v-model="focus" size="small" class="param" aria-label="侧重点"><el-option v-for="option in ['全部','义务与期限','金额与付款','风险']" :key="option" :label="`侧重：${option}`" :value="option" /></el-select>
          <el-select v-if="template === 'reply'" v-model="tone" size="small" class="param" aria-label="回复语气"><el-option v-for="option in ['正式','简洁','友好坚定']" :key="option" :label="option" :value="option" /></el-select>
          <el-select v-if="template === 'reply'" v-model="language" size="small" class="param" aria-label="回复语言"><el-option v-for="option in ['中文','英文','同来函']" :key="option" :label="option" :value="option" /></el-select>
          <span class="hint where">{{ TEMPLATE_DESC[template] }}<template v-if="template === 'translate'"> · 云端 · 脱敏后发送</template> · {{ need }}</span>
          <el-button v-if="short && canPick" link size="small" type="primary" @click="pick(template === 'minutes' ? 'audio' : 'doc')">加入文件</el-button>
        </div>
        <el-input ref="questionInput" v-model="text" type="textarea" :autosize="{ minRows: hero ? 2 : 1, maxRows: 8 }" maxlength="4000" :show-word-limit="text.length > 3500" :placeholder="anchor ? '就这个问题继续讨论，例如：怎么改更稳妥？' : hero ? '问一个问题，或把合同、报价单拖进来、粘贴进来…' : '继续追问…'" aria-label="问题或来函" @keydown="onKeydown" />
        <!-- 文件还在处理时：自动发送写在框内（问题 12） -->
        <p v-if="waitingText" class="auto" role="status"><Loader2 :size="14" :stroke-width="2" class="spin" aria-hidden="true" />{{ waitingText }}<span class="dot">·</span><el-button link size="small" @click="cancelWaiting">取消</el-button></p>
        <div class="bar">
          <button v-if="!anchor" type="button" class="cp-btn" :disabled="!!session && (session.files.length >= 10 || !!session.generating || session.review_running)" @click="pick()"><Paperclip :size="16" :stroke-width="1.75" />添加文件</button>
          <input ref="picker" class="file-input" type="file" multiple :accept="accept" @change="picked">
          <span v-if="localOnly" class="local-tag"><Lock :size="13" :stroke-width="2" />只在本机</span>
          <el-dropdown v-else trigger="click" :disabled="modelLocked" @visible-change="onModelMenu" @command="pickModel">
            <button type="button" class="cp-btn ai-pick" :disabled="modelLocked" aria-label="助手模型"><Sparkle :size="14" :stroke-width="2" /><span class="ai-name">{{ modelName }}</span><ChevronDown v-if="!modelLocked" :size="14" :stroke-width="1.75" /></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="model in allowedModels" :key="model.id" :command="model.id" :class="{ 'is-current': model.id === modelId }">{{ model.display_name }}</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
          <button v-if="session?.kb_enabled" type="button" class="cp-btn tog" :class="{ on: useKb }" :aria-pressed="useKb" @click="useKb = !useKb"><i class="sw" />知识库</button>
          <button type="button" class="cp-btn tog" :class="{ on: useHistory }" :aria-pressed="useHistory" @click="useHistory = !useHistory"><i class="sw" />参考历史合同</button>
          <button v-if="generating !== null" type="button" class="send stop" aria-label="停止生成" title="停止生成" @click="emit('stop', generating)"><Square :size="12" :stroke-width="2" fill="currentColor" /></button>
          <button v-else type="button" class="send" :class="{ dim: !!reason && !processing }" aria-label="发送" :title="reason || '发送（Enter）'" :disabled="(!!reason && !processing) || busy || sending" @click="submit"><Loader2 v-if="busy || sending" :size="18" :stroke-width="2" class="spin" /><ArrowUp v-else :size="18" :stroke-width="2.25" /></button>
        </div>
      </div>
      <p v-if="!waitingText && reason && (text.trim() || template)" class="hint status">{{ reason }}</p>
      <el-alert v-if="error && !open" :title="error" type="error" :closable="false" />
      <el-button v-if="blockedCode === 'DISCUSSION_ADVICE_BLOCKED'" :disabled="busy || sending" @click="withoutAdvice">不带该问题的结论与建议条文发送</el-button>
      <p v-if="omitAdvice && anchor" class="hint">本轮只附相关原文，不附问题结论与建议条文。</p>
    </template>
    <el-dialog v-model="open" title="确认发送内容" width="640px" :close-on-click-modal="false" :close-on-press-escape="!sending" :show-close="!sending" append-to-body>
      <p class="hint">消息里有疑似名称或金额，请逐项决定是否遮住。发给模型的只有遮住后的文字。</p><pre>{{ preview?.redacted }}</pre>
      <div v-for="(suspect, index) in preview?.suspects" :key="index" class="suspect">
        <p>{{ suspect.text }}</p><el-radio-group v-model="decisions[index]!.decision"><el-radio value="replace">遮住</el-radio><el-radio value="keep">不遮住</el-radio></el-radio-group>
        <el-select v-if="decisions[index]!.decision === 'replace'" v-model="decisions[index]!.category" aria-label="类别"><el-option v-for="option in toOptions(USER_CATEGORY_OPTIONS)" :key="option.value" :value="option.value" :label="option.label" /></el-select>
        <p v-if="suspect.in_files && decisions[index]!.decision === 'replace'" class="hint">文件中的同一文字也会遮住</p>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <template #footer><el-button :disabled="sending" @click="open = false">取消</el-button><el-button type="primary" :disabled="!confirmed" :loading="sending" @click="send">确认发送</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 一个容器：细边、浮起一层；首页圆角 24、完整档毛玻璃（澄明 8.2），对话页圆角 18 */
.box { display: flex; flex-direction: column; gap: 8px; padding: 14px 14px 10px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-2); transition: border-color var(--si-d2) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out); }
.is-hero .box { padding: 16px 16px 12px; border-radius: var(--si-radius-composer); }
.is-anchor .box { padding: 10px 12px 8px; border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.box:focus-within { border-color: var(--si-brand-line); box-shadow: var(--si-shadow-2), var(--si-ring); }
.box.dragging { border-color: var(--si-brand-2); border-style: dashed; background: var(--si-blue-tint); }
.box :deep(.el-textarea__inner) { padding: 4px 6px; border: 0; box-shadow: none; background: transparent; font: var(--si-font-body); font-size: calc(15px * var(--si-fs)); line-height: 1.7; color: var(--si-ink-1); resize: none; }
.is-hero .box :deep(.el-textarea__inner) { font-size: calc(16px * var(--si-fs)); line-height: 1.65; }
/* 文件标签 */
.files { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 8px; }
.ft { position: relative; display: flex; align-items: center; min-width: 0; overflow: hidden; border: 1px solid var(--si-hair); border-radius: 12px; background: var(--si-surface-1); transition: border-color var(--si-d2), background-color var(--si-d2); }
.ft-main { display: flex; flex: 1; align-items: center; gap: 10px; min-width: 0; padding: 8px 6px 8px 8px; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.ft-main:disabled { cursor: default; }
.fi { display: grid; flex: none; place-items: center; width: 32px; height: 32px; border-radius: 8px; }
.ft-pdf .fi { background: var(--si-danger-soft); color: var(--si-danger); }
.ft-doc .fi { background: var(--si-k-org-bg); color: var(--si-k-org); }
.ft-xls .fi { background: var(--si-ok-soft); color: var(--si-ok); }
.ft-img .fi { background: var(--si-ai-soft); color: var(--si-ai); }
.ft-aud .fi { background: var(--si-m-meet-soft); color: var(--si-m-meet); }
.ft-text { display: flex; flex-direction: column; min-width: 0; }
.nm { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label-sm); text-overflow: ellipsis; white-space: nowrap; }
.st { display: inline-flex; align-items: center; gap: 4px; overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); font-size: calc(12.5px * var(--si-fs)); text-overflow: ellipsis; white-space: nowrap; }
.ft--busy .st { color: var(--si-blue-ink); }
.ft--danger { border-color: var(--si-danger-line); }
.ft--danger .st { color: var(--si-danger-ink); }
.ft--warn { border-color: var(--si-warn-line); background: var(--si-warn-soft); }
.ft--warn .st, .ft-go { color: var(--si-warn-ink); font-weight: 500; }
.ft-go { flex: none; }
.ft--off .nm { color: var(--si-ink-3); }
.ft--off .fi { background: var(--si-surface-2); color: var(--si-ink-4); }
.ft-x { display: grid; flex: none; place-items: center; width: 24px; height: 24px; margin-right: 6px; padding: 0; border: 0; border-radius: 6px; background: none; color: var(--si-ink-4); cursor: pointer; }
.ft-x:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.ft-bar { position: absolute; right: 0; bottom: 0; left: 0; height: 2px; }
.ft-bar i { display: block; height: 100%; border-radius: 2px; background: linear-gradient(90deg, var(--si-brand-2), var(--si-ai)); transition: width var(--si-d3) var(--si-ease-out); }
.template-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.template-tag { display: inline-flex; align-items: center; gap: 2px; padding: 2px 0 2px 10px; border-radius: 999px; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-label-sm); }
.template-tag .ft-x { width: 20px; height: 20px; margin-right: 2px; color: inherit; }
.param { width: 132px; }
.where { margin: 0; }
.auto { display: flex; align-items: center; gap: 8px; margin: 0; padding: 8px 6px 0; border-top: 1px dashed var(--si-hair); color: var(--si-ink-3); font: var(--si-font-caption); }
.auto svg { color: var(--si-brand-2); }
.auto .dot { color: var(--si-ink-4); }
/* 底栏 */
.bar { display: flex; align-items: center; gap: 4px; }
.cp-btn { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 10px; border: 0; border-radius: var(--si-radius-control); background: none; color: var(--si-ink-2); font: var(--si-font-body); font-size: calc(13.5px * var(--si-fs)); cursor: pointer; transition: background-color var(--si-d2), box-shadow var(--si-d2); }
.cp-btn:hover:not(:disabled) { background: var(--si-hover-bg); }
.cp-btn:disabled { cursor: default; opacity: .6; }
.cp-btn > svg { color: var(--si-ink-3); }
.ai-pick { max-width: 220px; background: var(--si-ai-soft); color: var(--si-ai-ink); }
.ai-pick:hover:not(:disabled) { background: var(--si-ai-soft); box-shadow: inset 0 0 0 1px var(--si-ai-line); }
.ai-pick:disabled { opacity: 1; }
.ai-pick > svg { color: var(--si-ai); }
.ai-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.local-tag { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border-radius: 999px; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-badge); }
.sw { position: relative; flex: none; width: 28px; height: 16px; border-radius: 999px; background: var(--si-hair-strong); transition: background-color var(--si-d2); }
.sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; border-radius: 50%; background: var(--si-white); transition: transform var(--si-d3) var(--si-ease-spring); }
.tog.on .sw { background: var(--si-brand); }
.tog.on .sw::after { transform: translateX(12px); }
.send { display: inline-grid; flex: none; place-items: center; width: 40px; height: 40px; margin-left: auto; padding: 0; border: 0; border-radius: 50%; background: var(--si-brand); color: var(--si-white); box-shadow: var(--si-btn-shadow); cursor: pointer; transition: transform var(--si-d1) var(--si-ease-out), background-color var(--si-d2); }
.send:hover:not(:disabled) { background: var(--si-blue-press); }
.send:active:not(:disabled) { transform: scale(.92); }
.send.dim, .send:disabled { background: var(--si-hair-strong); box-shadow: none; cursor: default; }
.send.stop { background: var(--si-ink-1); color: var(--si-bg-canvas); box-shadow: none; }
.is-anchor .send { width: 32px; height: 32px; }
.failure { display: flex; align-items: center; gap: 8px; margin: 0; color: var(--si-danger-ink); font: var(--si-font-caption); }
.failure-text { min-width: 0; overflow-wrap: anywhere; }
.spin { animation: cp-spin 1s linear infinite; }
@keyframes cp-spin { to { transform: rotate(360deg); } }
.file-input { display: none; }
.status { margin: 6px 0 0; }
pre { padding: 16px; overflow-y: auto; max-height: 320px; border-radius: var(--el-border-radius-base); background: var(--si-surface-2); font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
.suspect { padding: 12px 0; border-top: 1px solid var(--si-hair); }
.suspect :deep(.el-select) { width: 160px; }
</style>
