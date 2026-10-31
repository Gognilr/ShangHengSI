<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { modelsApi, type AvailableModel } from '@/api/models'
import { ArrowUp, Square } from '@lucide/vue'
import { assistantApi, type Session, type Template, type MessageInput } from '@/api/assistant'
import type { ChatPreview, ChatDecision } from '@/api/chat'
import type { UserCategory } from '@/api/redaction'
import { ApiError } from '@/api/http'
import { USER_CATEGORY_OPTIONS, toOptions } from '@/utils/labels'
import TemplatePicker from './TemplatePicker.vue'
const props = defineProps<{ session: Session; template: Template | null; generating: number | null }>()
const emit = defineEmits<{ 'update:template': [value: Template | null]; sent: []; stop: [mid: number] }>()
const modelItems = ref<AvailableModel[]>([])
const modelId = ref<number>()
const modelError = ref('')
const modelLoading = ref(false)
const allowedModels = computed(() => modelItems.value.filter(m => props.template !== 'compare' || m.structured_ok))
let modelVersion = 0
function selectAvailable(preferred?: number | null) {
  modelId.value = allowedModels.value.find(m => m.id === preferred)?.id
    ?? allowedModels.value.find(m => m.is_default)?.id ?? allowedModels.value[0]?.id
}
watch(() => props.session.id, async id => {
  const version = ++modelVersion
  modelLoading.value = true
  modelError.value = ''
  modelId.value = undefined
  try {
    const [list, history] = await Promise.all([modelsApi.list('assist'), assistantApi.messages(id)])
    if (version !== modelVersion) return
    modelItems.value = list.items
    selectAvailable([...history.items].reverse().find(m => m.role === 'assistant' && m.model_id)?.model_id)
  } catch (e) { if (version === modelVersion) modelError.value = e instanceof ApiError ? e.message : '模型列表加载失败，请刷新页面' }
  finally { if (version === modelVersion) modelLoading.value = false }
}, { immediate: true })
watch(() => props.template, template => {
  if (modelLoading.value || !modelItems.value.length) return
  if (!allowedModels.value.some(m => m.id === modelId.value)) {
    selectAvailable()
    if (template === 'compare') ElMessage.info(modelId.value ? '比价需要结构化输出，已切换可用模型' : '没有支持结构化输出的助手模型，请联系管理员')
  }
})
const text = ref(''), fileIds = ref<string[]>([]), target = ref('英文'), focus = ref('全部'), tone = ref('正式'), language = ref('同来函')
const useKb = ref(false), useHistory = ref(false), busy = ref(false), sending = ref(false), error = ref(''), open = ref(false)
const preview = ref<ChatPreview | null>(null)
const decisions = ref<{ decision: '' | 'replace' | 'keep'; category: UserCategory | '' }[]>([])
const confirmed = computed(() => decisions.value.every(d => d.decision === 'keep' || (d.decision === 'replace' && d.category)))
const files = computed(() => props.session.files.filter(f => f.confirmed_at))
watch(() => props.session.id, () => { text.value = ''; useHistory.value = false; useKb.value = !!props.session.kb_enabled; fileIds.value = files.value.map(f => f.id) }, { immediate: true })
watch(() => props.session.kb_enabled, value => { useKb.value = !!value })
watch(() => files.value.map(f => f.id).join(','), () => { fileIds.value = files.value.map(f => f.id) })
const limits = computed(() => props.template === 'translate' ? [1, 1] : props.template === 'summary' ? [1, 3] : props.template === 'reply' ? [0, 3] : props.template ? [2, 6] : [0, 10])
const reason = computed(() => modelLoading.value ? '正在加载模型列表' : modelError.value ? modelError.value : !modelId.value ? '没有可用的助手模型，请联系管理员' : !props.session.can_send ? props.session.send_reason ?? '当前不能发送' : fileIds.value.length < limits.value[0]! || fileIds.value.length > limits.value[1]! ? `请选择 ${limits.value[0]}～${limits.value[1]} 个已确认文件` : !text.value.trim() && (!props.template || props.template === 'reply' && !fileIds.value.length) ? '请输入问题或粘贴来函' : '')
let pending: MessageInput | null = null
function input(): MessageInput {
  const params: Record<string, unknown> = props.template === 'translate'
    ? { file_id: fileIds.value[0], target: target.value }
    : { file_ids: [...fileIds.value] }
  if (props.template === 'summary') params.focus = focus.value
  if (props.template === 'reply') Object.assign(params, { tone: tone.value, language: language.value })
  return { model_id: modelId.value, message: text.value, template: props.template, params, use_kb: useKb.value && !!props.session.kb_enabled, use_history: useHistory.value }
}
async function submit() {
  if (reason.value || busy.value) return
  busy.value = true; error.value = ''; pending = input()
  try {
    preview.value = await assistantApi.preview(props.session.id, pending)
    decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' }))
    open.value = true
  } catch (e) { error.value = e instanceof ApiError ? e.message : '预览失败，请重试' }
  finally { busy.value = false }
}
// Enter 打开“确认发送内容”，Shift+Enter 换行；输入法组字期间的 Enter 不处理
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (props.generating === null) void submit()
}
async function send() {
  if (!pending || !confirmed.value || sending.value) return
  sending.value = true; error.value = ''
  const selected: ChatDecision[] = (preview.value?.suspects ?? []).map((suspect, index) => ({
    start: suspect.start, end: suspect.end, decision: decisions.value[index]!.decision as 'replace' | 'keep',
    ...(decisions.value[index]!.category ? { category: decisions.value[index]!.category as UserCategory } : {}),
  }))
  try { await assistantApi.send(props.session.id, { ...pending, decisions: selected }); open.value = false; text.value = ''; pending = null; emit('sent') }
  catch (e) {
    error.value = e instanceof ApiError ? e.message : '发送失败，请重试'
    if (e instanceof ApiError && e.code === 'CHAT_SUSPECTS_PENDING' && pending) {
      try {
        preview.value = await assistantApi.preview(props.session.id, pending)
        decisions.value = preview.value.suspects.map(() => ({ decision: '', category: '' }))
      } catch (freshError) { error.value = freshError instanceof ApiError ? freshError.message : '预览更新失败，请取消后重试' }
    }
  }
  finally { sending.value = false }
}
</script>
<template>
  <div class="composer">
    <p v-if="session.original_deleted" class="hint">原件已按保留期限删除，本会话只能查看</p>
    <template v-else>
      <div v-if="files.length || template === 'translate' || template === 'summary' || template === 'reply'" class="parameters">
        <el-select v-model="fileIds" multiple collapse-tags collapse-tags-tooltip placeholder="使用的文件" aria-label="使用的文件"><el-option v-for="file in files" :key="file.id" :label="`文件 ${file.seq}：${file.original_name}`" :value="file.id" /></el-select>
        <el-select v-if="template === 'translate'" v-model="target" aria-label="目标语言"><el-option v-for="option in ['中文','英文','日文','韩文','俄文','西班牙文','法文','德文','阿拉伯文']" :key="option" :label="option" :value="option" /></el-select>
        <el-select v-if="template === 'summary'" v-model="focus" aria-label="侧重点"><el-option v-for="option in ['全部','义务与期限','金额与付款','风险']" :key="option" :label="option" :value="option" /></el-select>
        <el-select v-if="template === 'reply'" v-model="tone" aria-label="回复语气"><el-option v-for="option in ['正式','简洁','友好坚定']" :key="option" :label="option" :value="option" /></el-select>
        <el-select v-if="template === 'reply'" v-model="language" aria-label="回复语言"><el-option v-for="option in ['中文','英文','同来函']" :key="option" :label="option" :value="option" /></el-select>
      </div>
      <div class="input-box">
        <el-input v-model="text" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }" maxlength="4000" :show-word-limit="text.length > 3500" placeholder="输入问题或粘贴来函" aria-label="问题或来函" @keydown="onKeydown" />
        <el-button v-if="generating !== null" class="send" circle aria-label="停止生成" title="停止生成" @click="emit('stop', generating)"><Square :size="14" :stroke-width="2" fill="currentColor" /></el-button>
        <el-button v-else class="send" type="primary" circle aria-label="发送" :title="reason || '发送'" :disabled="!!reason || sending" :loading="busy" @click="submit"><ArrowUp v-if="!busy" :size="16" :stroke-width="2" /></el-button>
      </div>
      <div class="options">
        <el-select v-model="modelId" class="model-select" size="small" aria-label="助手模型" placeholder="选择模型" :loading="modelLoading" :disabled="modelLoading || !allowedModels.length || sending || busy || generating !== null"><el-option v-for="model in allowedModels" :key="model.id" :value="model.id" :label="model.display_name" /></el-select>
        <TemplatePicker :model-value="template" @update:model-value="emit('update:template', $event)" />
        <el-checkbox v-if="session.kb_enabled" v-model="useKb">附带公司知识库</el-checkbox><el-checkbox v-model="useHistory">参考我的历史合同</el-checkbox>
        <span class="keys">Enter 发送，Shift+Enter 换行</span>
      </div>
      <p v-if="reason && (text.trim() || template || !session.can_send)" class="hint">{{ reason }}</p><el-alert v-if="error && !open" :title="error" type="error" :closable="false" />
    </template>
    <el-dialog v-model="open" title="确认发送内容" width="640px" :close-on-click-modal="false" :close-on-press-escape="!sending" :show-close="!sending" append-to-body>
      <p class="hint">发给模型的只有脱敏后的文字，请核对后发送。</p><pre>{{ preview?.redacted }}</pre>
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
.model-select { width: 160px; }
.composer { padding: 12px 24px 16px; border-top: 1px solid var(--el-border-color-light); background: var(--el-bg-color); }
.composer > * { max-width: 760px; margin-left: auto; margin-right: auto; }
.parameters { display: flex; gap: 8px; margin-bottom: 8px; }
.parameters :deep(.el-select) { min-width: 0; flex: 1; }
.input-box { position: relative; }
.input-box :deep(.el-textarea__inner) { padding: 10px 52px 10px 12px; font: var(--si-font-body); resize: none; }
.input-box :deep(.el-input__count) { right: 52px; }
.send { position: absolute; right: 8px; bottom: 6px; width: 32px; height: 32px; }
.options { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; }
.options :deep(.el-checkbox) { margin-right: 0; }
.keys { margin-left: auto; font: var(--si-font-caption); color: var(--el-text-color-placeholder); }
.hint { margin: 6px 0 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: var(--si-font-body); max-height: 320px; overflow-y: auto; padding: 16px; background: var(--el-fill-color-light); border-radius: var(--el-border-radius-base); }
.suspect { border-top: 1px solid var(--el-border-color-light); padding: 12px 0; }
.suspect :deep(.el-select) { width: 160px; }
</style>
