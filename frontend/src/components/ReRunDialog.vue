<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { MAX_NOTE_LENGTH, tasksApi, type Source, type Stance, type TaskDetail } from '@/api/tasks'
import { SOURCE_OPTIONS, STANCE_OPTIONS, toOptions } from '@/utils/labels'
import { redactionApi, type RedactionInfo } from '@/api/redaction'
import RedactionCompare from './RedactionCompare.vue'

/**
 * 重新审核对话框（SPEC 6.3、7.4，设计 docs/design/M5.md 第七节）。
 * 默认取任务当前的立场、来源和本次补充说明；确定后任务回到待确认，跳到新建审核页的第二步再次确认发送。
 */
const props = defineProps<{ modelValue: boolean; task: TaskDetail }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const router = useRouter()
const submitting = ref(false)
const errorText = ref('')
const comparison = ref<RedactionInfo>({ items: [], summary: {}, suspects: [] })
const outboundText = ref('')
const previewLoading = ref(false)
const previewError = ref('')
let previewVersion = 0
async function loadComparison() {
  const current = ++previewVersion
  previewLoading.value = true
  previewError.value = ''
  outboundText.value = ''
  try {
    const [info, outbound] = await Promise.all([redactionApi.redaction(props.task.id), redactionApi.outbound(props.task.id)])
    if (current !== previewVersion) return
    comparison.value = info
    outboundText.value = outbound.text
  } catch (e) {
    if (current === previewVersion) previewError.value = e instanceof ApiError ? e.message : '加载失败，请关闭后重试'
  } finally { if (current === previewVersion) previewLoading.value = false }
}
const form = reactive<{ stance: Stance; source: Source; userNote: string }>({
  stance: 'sales',
  source: 'outgoing',
  userNote: '',
})

const stanceOptions = toOptions(STANCE_OPTIONS)
const sourceOptions = toOptions(SOURCE_OPTIONS)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// 每次打开都按任务当前值重新填写
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      form.stance = props.task.stance
      form.source = props.task.source
      form.userNote = props.task.user_note ?? ''
      errorText.value = ''
      void loadComparison()
    } else {
      previewVersion++
    }
  },
  { immediate: true },
)

async function submit() {
  errorText.value = ''
  submitting.value = true
  try {
    await tasksApi.patch(props.task.id, {
      stance: form.stance,
      source: form.source,
      user_note: form.userNote.trim(),
    })
    visible.value = false
    await router.push({ name: 'review-new', query: { task: props.task.id } })
  } catch (e) {
    errorText.value = e instanceof ApiError ? e.message : '提交失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="重新审核" width="640px" top="24px" class="rerun-dialog u7-rerundialog" :close-on-click-modal="false" append-to-body>
    <el-form label-width="96px" @submit.prevent="submit">
      <el-form-item label="立场">
        <el-radio-group v-model="form.stance">
          <el-radio v-for="o in stanceOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="来源">
        <el-radio-group v-model="form.source">
          <el-radio v-for="o in sourceOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="本次补充说明">
        <el-input
          v-model="form.userNote"
          type="textarea"
          :rows="4"
          :maxlength="MAX_NOTE_LENGTH"
          show-word-limit
        />
      </el-form-item>
    </el-form>
    <p class="hint">确定后将按新的设置重新生成外发文本，需在第二步再次确认发送。之前的审核结果会保留。</p>
    <section class="current-comparison">
      <h3>当前任务的原文与外发文本对照</h3>
      <!-- Q-149：当前预览只读；编辑表单后仍由既有 PATCH 流程重算，再在第二步确认。 -->
      <p class="hint">以下为当前任务的文本；尚未应用本次修改的设置。</p>
      <el-alert v-if="previewError" type="error" :closable="false" show-icon :title="previewError" />
      <RedactionCompare v-if="modelValue" :task-id="task.id" :files="task.files" :outbound-text="outboundText" :items="comparison.items" :suspects="comparison.suspects" :loading="previewLoading" :editable="false" />
    </section>
    <el-alert v-if="errorText" type="error" :closable="false" show-icon :title="errorText" class="gap" />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">确定</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.u7-rerundialog .el-dialog__title), :global(.u7-rerundialog .el-drawer__title) { font: var(--si-font-h2); }
:global(.u7-rerundialog .el-dialog__body), :global(.u7-rerundialog .el-drawer__body) { font: var(--si-font-body); }

/* el-dialog 的根节点被 Teleport 移出组件，按唯一类名限定才能命中。 */
:global(.rerun-dialog) { max-height: calc(100vh - 48px); margin-bottom: 24px; display: flex; flex-direction: column; }
:global(.rerun-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; flex: 1 1 auto; }
:global(.rerun-dialog .el-dialog__header), :global(.rerun-dialog .el-dialog__footer) { flex: none; }
.current-comparison { margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--el-border-color-light); }
h3 { margin: 0 0 12px; font: var(--si-font-h2); }
</style>
