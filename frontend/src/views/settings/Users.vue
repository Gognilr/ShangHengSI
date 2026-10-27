<script setup lang="ts">
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { adminUsersApi, type UserRow } from '@/api/admin'
import type { Role } from '@/api/auth'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { ROLE_OPTIONS, roleLabel, toOptions, userStatusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import { Plus } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'

const auth = useAuthStore()
const items = ref<UserRow[]>([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = (await adminUsersApi.list()).items
  } catch (e) {
    error.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面'
  } finally {
    loading.value = false
  }
}
onMounted(load)

const isSelf = (row: UserRow) => row.id === auth.user?.id

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

// ---- 新增 / 编辑 ----
const formRef = ref<FormInstance>()
const dialog = ref<'' | 'create' | 'edit'>('')
const editing = ref<UserRow | null>(null)
const submitting = ref(false)
const form = reactive({ username: '', display_name: '', password: '', role: 'user' as Role })

function validatePassword(_rule: unknown, value: string, callback: (e?: Error) => void) {
  if (value.length < 8) return callback(new Error('密码至少 8 位'))
  if (value.length > 128) return callback(new Error('密码不能超过 128 位'))
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) return callback(new Error('密码须同时包含字母和数字'))
  callback()
}

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { max: 50, message: '用户名不能超过 50 个字符', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_.@-]+$/, message: '用户名只能包含字母、数字和 _ . @ -', trigger: 'blur' },
  ],
  display_name: [
    { required: true, whitespace: true, message: '请输入显示名', trigger: 'blur' },
    { max: 50, message: '显示名不能超过 50 个字符', trigger: 'blur' },
  ],
  password: [{ required: true, validator: validatePassword, trigger: 'blur' }],
}

function openCreate() {
  Object.assign(form, { username: '', display_name: '', password: '', role: 'user' })
  editing.value = null
  dialog.value = 'create'
  formRef.value?.clearValidate()
}

function openEdit(row: UserRow) {
  Object.assign(form, { username: row.username, display_name: row.display_name, password: '', role: row.role })
  editing.value = row
  dialog.value = 'edit'
  formRef.value?.clearValidate()
}

async function submit() {
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    if (dialog.value === 'create') {
      await adminUsersApi.create({
        username: form.username.trim(),
        display_name: form.display_name.trim(),
        password: form.password,
        role: form.role,
      })
      ElMessage.success('账号已创建，首次登录须修改密码')
    } else if (editing.value) {
      const row = editing.value
      const body: Record<string, string> = {}
      if (form.display_name.trim() !== row.display_name) body.display_name = form.display_name.trim()
      if (!isSelf(row) && form.role !== row.role) body.role = form.role
      if (Object.keys(body).length) await adminUsersApi.update(row.id, body)
      ElMessage.success('已保存')
    }
    dialog.value = ''
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  } finally {
    submitting.value = false
  }
}

// ---- 停用 / 启用 ----
async function toggleStatus(row: UserRow) {
  const disabling = row.status === 'active'
  try {
    if (disabling) {
      await ElMessageBox.confirm(
        `停用后「${row.display_name}」将立即退出登录且无法再登录，已有的审核数据保留。`,
        '停用账号',
        { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
      )
    }
  } catch {
    return
  }
  try {
    await adminUsersApi.update(row.id, { status: disabling ? 'disabled' : 'active' })
    ElMessage.success(disabling ? '账号已停用' : '账号已启用')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '操作失败，请稍后重试'))
  }
}

// ---- 重置密码 ----
const resetRef = ref<FormInstance>()
const resetTarget = ref<UserRow | null>(null)
const resetForm = reactive({ password: '' })
const resetting = ref(false)

function openReset(row: UserRow) {
  resetTarget.value = row
  resetForm.password = ''
  resetRef.value?.clearValidate()
}

async function submitReset() {
  if (!resetTarget.value || !resetRef.value || !(await resetRef.value.validate().catch(() => false))) return
  resetting.value = true
  try {
    await adminUsersApi.resetPassword(resetTarget.value.id, resetForm.password)
    ElMessage.success('密码已重置，该账号须在下次登录时修改密码')
    resetTarget.value = null
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '重置失败，请稍后重试'))
  } finally {
    resetting.value = false
  }
}

const dialogTitle = computed(() => (dialog.value === 'create' ? '新增账号' : '编辑账号'))
</script>

<template>
  <div class="settings-page">
    <PageHeader title="账号管理" description="新建账号、启停和重置密码；新账号首次登录时须先修改密码"><el-button type="primary" :icon="Plus" @click="openCreate">新增账号</el-button></PageHeader>
    <el-card shadow="never">
      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" class="gap" />
      <el-table v-loading="loading" :data="items" empty-text="暂无账号">
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column label="显示名" min-width="140"><template #default="{ row }"><div class="user-name"><span class="avatar">{{ Array.from(row.display_name)[0] ?? '?' }}</span><span>{{ row.display_name }}</span></div></template></el-table-column>
        <el-table-column label="角色" width="100">
          <template #default="{ row }"><el-tag type="info" size="small">{{ roleLabel(row.role) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
              {{ userStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录时间" width="170" class-name="num">
          <template #default="{ row }">{{ formatDateTime(row.last_login_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="!isSelf(row)" link :type="row.status === 'active' ? 'danger' : 'primary'" @click="toggleStatus(row)">
              {{ row.status === 'active' ? '停用' : '启用' }}
            </el-button>
            <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog :model-value="dialog !== ''" :title="dialogTitle" width="480px" :close-on-click-modal="false" @close="dialog = ''">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px" @submit.prevent="submit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="dialog === 'edit'" maxlength="50" autocomplete="off" />
        </el-form-item>
        <el-form-item label="显示名" prop="display_name">
          <el-input v-model="form.display_name" maxlength="50" show-word-limit autocomplete="off" />
        </el-form-item>
        <el-form-item v-if="dialog === 'create'" label="初始密码" prop="password">
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="form.role" :disabled="dialog === 'edit' && editing !== null && isSelf(editing)">
            <el-radio v-for="o in toOptions(ROLE_OPTIONS)" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <div v-if="dialog === 'create'" class="hint">新账号首次登录时须先修改密码。</div>
      </el-form>
      <template #footer>
        <el-button @click="dialog = ''">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog :model-value="resetTarget !== null" title="重置密码" width="480px" :close-on-click-modal="false" @close="resetTarget = null">
      <el-form ref="resetRef" :model="resetForm" :rules="{ password: rules.password }" label-width="110px" @submit.prevent="submitReset">
        <el-form-item label="账号">{{ resetTarget?.username }}（{{ resetTarget?.display_name }}）</el-form-item>
        <el-form-item label="新密码" prop="password">
          <el-input v-model="resetForm.password" type="password" show-password autocomplete="new-password" placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <div class="hint">重置后该账号会立即退出登录，下次登录须修改密码。</div>
      </el-form>
      <template #footer>
        <el-button @click="resetTarget = null">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="submitReset">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
:deep(.el-dialog__title), :deep(.el-drawer__title) { font: var(--si-font-h2); }
:deep(.el-dialog__body), :deep(.el-drawer__body) { font: var(--si-font-body); }

.user-name { display: flex; align-items: center; gap: 8px; overflow-wrap: anywhere; }
</style>
