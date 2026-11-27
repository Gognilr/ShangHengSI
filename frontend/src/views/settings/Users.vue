<script setup lang="ts">
import SiBadge from '@/components/ui/SiBadge.vue'
import SiNotice from '@/components/ui/SiNotice.vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { adminRolesApi, adminUsersApi, type RoleRow, type UserRow } from '@/api/admin'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { userStatusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'
import { Plus } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import RowMore from '@/components/RowMore.vue'
import SiRowCard from '@/components/ui/SiRowCard.vue'

const auth = useAuthStore()
const items = ref<UserRow[]>([])
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
const roles = ref<RoleRow[]>([])
const roleName = (id: number) => roles.value.find(r => r.id === id)?.name ?? '已删除的角色'
const isAdminRole = (id: number) => { const b = roles.value.find(r => r.id === id)?.builtin; return b === 'sys_admin' || b === 'biz_admin' }
const defaultRole = computed(() => roles.value.find(r => r.builtin === 'user')?.id)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [users, roleList] = await Promise.all([adminUsersApi.list(), adminRolesApi.list()])
    items.value = users.items
    roles.value = roleList.items
  } catch (e) {
    error.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面'
  } finally {
    loading.value = false
    loaded.value = true
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
const form = reactive({ username: '', display_name: '', password: '', role_ids: [] as number[] })

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
  role_ids: [{ type: 'array', required: true, min: 1, message: '至少选一个角色', trigger: 'change' }],
}

function openCreate() {
  Object.assign(form, { username: '', display_name: '', password: '', role_ids: defaultRole.value ? [defaultRole.value] : [] })
  editing.value = null
  dialog.value = 'create'
  formRef.value?.clearValidate()
}

function openEdit(row: UserRow) {
  Object.assign(form, { username: row.username, display_name: row.display_name, password: '', role_ids: [...row.role_ids] })
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
        role_ids: form.role_ids,
      })
      ElMessage.success('账号已创建，首次登录须修改密码')
    } else if (editing.value) {
      const row = editing.value
      const body: { display_name?: string; role_ids?: number[] } = {}
      if (form.display_name.trim() !== row.display_name) body.display_name = form.display_name.trim()
      const sorted = [...form.role_ids].sort((a, b) => a - b)
      if (!isSelf(row) && sorted.join() !== [...row.role_ids].sort((a, b) => a - b).join()) body.role_ids = sorted
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

// ---- 强制下线（V31-U5F 7.2） ----
async function forceLogout(row: UserRow) {
  try {
    await ElMessageBox.confirm(`「${row.display_name}」在所有设备上的登录将立即失效，需要重新登录。`, '强制下线',
      { type: 'warning', confirmButtonText: '强制下线', cancelButtonText: '取消' })
  } catch { return }
  try {
    await adminUsersApi.forceLogout(row.id)
    ElMessage.success(isSelf(row) ? '已退出你的其他设备' : '已强制下线')
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
  <div class="settings-page users-page">
    <div class="head-row">
      <div><h2>账号</h2><p class="aux">{{ items.length }} 个账号 · 管理员也看不到他人的任务内容</p></div>
      <el-button type="primary" @click="openCreate"><template #icon><Plus :size="16" :stroke-width="1.75" /></template>新增账号</el-button>
    </div>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" class="gap" />
    <div v-if="!loaded" class="skel" aria-busy="true"><i v-for="n in 4" :key="n" /></div>
    <EmptyState v-else-if="!items.length" compact title="暂无账号" description="点击右上角“新增账号”添加。" />
    <div v-else v-loading="loading" class="rows">
      <SiRowCard v-for="row in items" :key="row.id" class="urow" :class="{ off: row.status !== 'active' }">
        <template #icon><span class="avatar" aria-hidden="true">{{ Array.from(row.display_name)[0] ?? '?' }}</span></template>
        <template #title>{{ row.display_name }}<SiBadge v-for="id in row.role_ids" :key="id" :tone="isAdminRole(id) ? 'ai' : 'neutral'">{{ roleName(id) }}</SiBadge></template>
        <template #sub>{{ row.username }}</template>
        <template #data>
          <span class="login"><span class="num">最近登录 {{ formatDateTime(row.last_login_at) }}</span><span class="aux num">{{ row.last_login_ip ?? '' }}<b v-if="row.online" class="online">在线 {{ row.online }} 处</b></span></span>
        </template>
        <template #status><SiBadge :tone="row.status === 'active' ? 'ok' : 'neutral'" dot>{{ userStatusLabel(row.status) }}</SiBadge></template>
        <template #actions>
          <el-button size="small" @click="openReset(row)">重置密码</el-button>
          <RowMore>
            <el-dropdown-item @click="openEdit(row)">修改角色</el-dropdown-item>
            <el-dropdown-item v-if="row.online" @click="forceLogout(row)">强制下线</el-dropdown-item>
            <el-dropdown-item v-if="!isSelf(row)" :class="{ 'danger-item': row.status === 'active' }" @click="toggleStatus(row)">{{ row.status === 'active' ? '停用' : '启用' }}</el-dropdown-item>
          </RowMore>
        </template>
      </SiRowCard>
    </div>

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
        <el-form-item label="角色" prop="role_ids">
          <el-checkbox-group v-model="form.role_ids" :disabled="dialog === 'edit' && editing !== null && isSelf(editing)">
            <el-checkbox v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <div class="hint">可兼多个角色，权限取并集；各角色有哪些权限在“设置 → 角色”中调整。<template v-if="dialog === 'edit' && editing !== null && isSelf(editing)">不能修改自己的角色。</template></div>
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
.head-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.head-row h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.aux { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.rows { display: flex; flex-direction: column; gap: 10px; }
.skel { display: flex; flex-direction: column; gap: 10px; }
.skel i { display: block; height: 72px; border-radius: var(--si-radius-card); background: var(--si-hover-bg); }
.avatar { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-h4); }
.urow.off .avatar { background: var(--si-hover-bg); color: var(--si-ink-4); }
.urow.off :deep(.rc-n b) { color: var(--si-ink-3); }
.login { display: flex; flex-direction: column; width: 220px; font: var(--si-font-meta); color: var(--si-ink-2); }
.online { margin-left: 8px; font-weight: 500; color: var(--si-ok-ink, var(--si-ok)); }
</style>
