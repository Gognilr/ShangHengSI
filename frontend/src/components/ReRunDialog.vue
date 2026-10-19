<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { MAX_NOTE_LENGTH, tasksApi, type Source, type Stance, type TaskDetail } from '@/api/tasks'
import { SOURCE_OPTIONS, STANCE_OPTIONS, toOptions } from '@/utils/labels'

/**
 * 重新审核对话框（SPEC 6.3、7.4，设计 docs/design/M5.md 第七节）。
 * 默认取任务当前的立场、来源和本次补充说明；确定后任务回到待确认，跳到新建审核页的第二步再次确认发送。
 */
const props = defineProps<{ modelValue: boolean; task: TaskDetail }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const router = useRouter()
const submitting = ref(false)
const errorText = ref('')
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
  <el-dialog v-model="visible" title="重新审核" width="480px" :close-on-click-modal="false" append-to-body>
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
    <el-alert v-if="errorText" type="error" :closable="false" show-icon :title="errorText" class="gap" />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">确定</el-button>
    </template>
  </el-dialog>
</template>
