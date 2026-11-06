<script setup lang="ts">
// 设置 · 角色（V31-U5F 7.1）：权限点由系统固定，角色勾选权限点，人在“账号”里挑角色（可兼多个）。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { Plus } from '@lucide/vue'
import { adminRolesApi, type PermissionDef, type RoleRow } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { Perm } from '@/utils/permissions'
import PageHeader from '@/components/PageHeader.vue'
import RowMore from '@/components/RowMore.vue'
import SettingSection from '@/components/SettingSection.vue'
import TableSkeleton from '@/components/TableSkeleton.vue'

const roles = ref<RoleRow[]>([])
const perms = ref<PermissionDef[]>([])
const loaded = ref(false)
const loading = ref(false)
const error = ref('')

const groups = computed(() => {
  const out: { name: string; items: PermissionDef[] }[] = []
  for (const p of perms.value) {
    const g = out.find(x => x.name === p.group) ?? out[out.push({ name: p.group, items: [] }) - 1]
    g.items.push(p)
  }
  return out
})
const permName = (code: Perm) => perms.value.find(p => p.code === code)?.name ?? code
const BUILTIN_NOTE: Record<string, string> = {
  user: '内置 · 新账号默认',
  biz_admin: '内置',
  sys_admin: '内置 · 至少保留一人',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await adminRolesApi.list()
    roles.value = res.items
    perms.value = res.permissions
  } catch (e) {
    error.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面'
  } finally {
    loading.value = false
    loaded.value = true
  }
}
onMounted(load)

// ---- 新建 / 编辑 ----
const editing = ref<RoleRow | null>(null)
const open = ref(false)
const saving = ref(false)
const form = reactive({ name: '', permissions: [] as Perm[] })

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', permissions: [] })
  open.value = true
}
function openEdit(row: RoleRow) {
  editing.value = row
  Object.assign(form, { name: row.name, permissions: [...row.permissions] })
  open.value = true
}
// “系统管理员”不能去掉“账号与角色”，否则没有人能再管账号
const locked = (code: Perm) => editing.value?.builtin === 'sys_admin' && code === 'sys.accounts'

async function save() {
  const name = form.name.trim()
  if (!name) { ElMessage.error('请输入角色名称'); return }
  saving.value = true
  try {
    const body = { name, permissions: form.permissions }
    if (editing.value) await adminRolesApi.update(editing.value.id, body)
    else await adminRolesApi.create(body)
    ElMessage.success(editing.value ? '已保存，持有该角色的人下次操作时生效' : '角色已创建')
    open.value = false
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

async function remove(row: RoleRow) {
  try {
    await ElMessageBox.confirm(`删除角色「${row.name}」？`, '删除角色', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    await adminRolesApi.delete(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '删除失败，请稍后重试')
  }
}
</script>

<template>
  <div class="settings-page">
    <PageHeader title="角色" description="按角色授权，人只挑角色；任何角色都不能查看他人的审核与会话内容">
      <el-button type="primary" @click="openCreate"><template #icon><Plus :size="16" :stroke-width="1.75" /></template>新建角色</el-button>
    </PageHeader>
    <div class="panel setting-panel">
      <SettingSection title="角色列表" description="权限点由系统固定；一个人兼多个角色时权限取并集。" stacked>
        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" class="gap" />
        <TableSkeleton v-if="!loaded" :columns="['160px', '1fr', '80px', '100px']" />
        <el-table v-else v-loading="loading" :data="roles">
          <el-table-column label="角色" min-width="160">
            <template #default="{ row }">
              <div class="name">{{ row.name }}</div>
              <div v-if="row.builtin" class="hint">{{ BUILTIN_NOTE[row.builtin] }}</div>
            </template>
          </el-table-column>
          <el-table-column label="权限" min-width="320">
            <template #default="{ row }">
              <div v-if="row.permissions.length" class="tags"><el-tag v-for="p in row.permissions" :key="p" size="small" type="info">{{ permName(p) }}</el-tag></div>
              <span v-else class="hint">没有任何权限</span>
            </template>
          </el-table-column>
          <el-table-column label="人数" width="80" class-name="num"><template #default="{ row }">{{ row.users }}</template></el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <RowMore v-if="!row.builtin"><el-dropdown-item class="danger-item" @click="remove(row)">删除</el-dropdown-item></RowMore>
            </template>
          </el-table-column>
        </el-table>
      </SettingSection>
    </div>

    <el-dialog v-model="open" :title="editing ? '编辑角色' : '新建角色'" width="560px" :close-on-click-modal="false">
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="名称"><el-input v-model="form.name" maxlength="30" show-word-limit /></el-form-item>
        <el-checkbox-group v-model="form.permissions">
          <div v-for="g in groups" :key="g.name" class="group">
            <div class="group-name">{{ g.name }}</div>
            <div class="group-items">
              <el-checkbox v-for="p in g.items" :key="p.code" :value="p.code" :disabled="locked(p.code)">{{ p.name }}</el-checkbox>
            </div>
          </div>
        </el-checkbox-group>
        <p v-if="editing?.builtin === 'sys_admin'" class="hint">“系统管理员”必须保留“账号与角色”，否则没有人能再管理账号。</p>
      </el-form>
      <template #footer>
        <el-button @click="open = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.name { font-weight: 600; color: var(--si-ink-1); }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.group { padding: 10px 0; border-top: 1px solid var(--si-hair); }
.group:first-child { border-top: 0; }
.group-name { font: var(--si-font-meta); color: var(--si-ink-3); margin-bottom: 6px; }
.group-items { display: flex; flex-wrap: wrap; column-gap: 4px; }
</style>
