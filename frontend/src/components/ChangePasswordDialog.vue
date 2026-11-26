<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

/**
 * 主动修改密码对话框（SPEC 6.6、open_questions Q-06）：账号菜单与设置 · 个人里打开。
 * 首次登录或被重置后的改密在登录卡第二步（澄明二版 6.3），不走这里。
 */
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const auth = useAuthStore()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const errorText = ref('')
const form = reactive({ oldPassword: '', newPassword: '', confirm: '' })

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// 新密码至少 8 位，须同时包含字母和数字；上限 128 位（open_questions Q-27）
function validateNew(_rule: unknown, value: string, callback: (e?: Error) => void) {
  if (value.length < 8) return callback(new Error('新密码至少 8 位'))
  if (value.length > 128) return callback(new Error('新密码不能超过 128 位'))
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) {
    return callback(new Error('新密码须同时包含字母和数字'))
  }
  callback()
}

function validateConfirm(_rule: unknown, value: string, callback: (e?: Error) => void) {
  callback(value === form.newPassword ? undefined : new Error('两次输入的新密码不一致'))
}

const rules = computed<FormRules>(() => ({
  oldPassword: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  newPassword: [{ required: true, validator: validateNew, trigger: 'blur' }],
  confirm: [{ required: true, validator: validateConfirm, trigger: 'blur' }],
}))

// 每次打开都清空上次的输入
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      form.oldPassword = ''
      form.newPassword = ''
      form.confirm = ''
      errorText.value = ''
      formRef.value?.clearValidate()
    }
  },
)

async function submit() {
  errorText.value = ''
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    await auth.changePassword(form.oldPassword, form.newPassword)
    ElMessage.success('密码已修改，其他设备上的登录已全部退出')
    visible.value = false
  } catch (e) {
    errorText.value = e instanceof ApiError ? e.message : '修改失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="修改密码"
    width="480px"
    :close-on-click-modal="false"
        destroy-on-close
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="96px" @submit.prevent="submit">
      <el-form-item label="原密码" prop="oldPassword">
        <el-input v-model="form.oldPassword" type="password" show-password autocomplete="current-password" />
      </el-form-item>
      <el-form-item label="新密码" prop="newPassword">
        <el-input
          v-model="form.newPassword"
          type="password"
          show-password
          autocomplete="new-password"
          placeholder="至少 8 位，含字母和数字"
        />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirm">
        <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <SiNotice v-if="errorText" type="error" :closable="false" :title="errorText" />
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">确定</el-button>
    </template>
  </el-dialog>
</template>
