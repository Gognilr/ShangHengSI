<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 设置 · 角色（V31-U5F 7.1；澄明二版 6.22）：权限点由系统固定；主图是功能 × 角色的勾选网格，改动过的格子高亮，页面底部固定“n 处改动 · 保存 / 放弃”。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Plus } from '@lucide/vue'
import { adminRolesApi, type PermissionDef, type RoleRow } from '@/api/admin'
import { ApiError } from '@/api/http'
import type { Perm } from '@/utils/permissions'
import RowMore from '@/components/RowMore.vue'

const roles = ref<RoleRow[]>([])
const perms = ref<PermissionDef[]>([])
const loaded = ref(false)
const loading = ref(false)
const error = ref('')
/** 草稿：角色 id → 勾选的权限点；与服务端不同的格子高亮 */
const draft = reactive<Record<number, Perm[]>>({})
const saving = ref(false)

const GROUP_COLOR: Record<string, string> = { 使用: 'var(--si-m-review)', 内容维护: 'var(--si-m-trans)', 脱敏维护: 'var(--si-ai)', 系统: 'var(--si-m-bid)' }
const groups = computed(() => {
  const out: { name: string; items: PermissionDef[] }[] = []
  for (const p of perms.value) {
    const g = out.find(x => x.name === p.group) ?? out[out.push({ name: p.group, items: [] }) - 1]
    g.items.push(p)
  }
  return out
})
const BUILTIN_NOTE: Record<string, string> = { user: '内置 · 新账号默认', biz_admin: '内置', sys_admin: '内置 · 至少保留一人' }
// “系统管理员”不能去掉“账号与角色”，否则没有人能再管账号
const locked = (r: RoleRow, code: Perm) => r.builtin === 'sys_admin' && code === 'sys.accounts'
const has = (r: RoleRow, code: Perm) => draft[r.id]?.includes(code) ?? false
const was = (r: RoleRow, code: Perm) => r.permissions.includes(code)
const changed = (r: RoleRow, code: Perm) => has(r, code) !== was(r, code)
const changeCount = computed(() => roles.value.reduce((n, r) => n + perms.value.filter(p => changed(r, p.code)).length, 0))

function toggle(r: RoleRow, code: Perm) {
  if (locked(r, code) || saving.value) return
  const list = draft[r.id] ?? (draft[r.id] = [])
  const at = list.indexOf(code)
  if (at >= 0) list.splice(at, 1); else list.push(code)
}
function resetDraft() { for (const r of roles.value) draft[r.id] = [...r.permissions] }

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await adminRolesApi.list()
    roles.value = res.items
    perms.value = res.permissions
    resetDraft()
  } catch (e) {
    error.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面'
  } finally {
    loading.value = false
    loaded.value = true
  }
}
onMounted(load)

async function saveAll() {
  saving.value = true
  try {
    for (const r of roles.value) {
      if (!perms.value.some(p => changed(r, p.code))) continue
      await adminRolesApi.update(r.id, { name: r.name, permissions: perms.value.map(p => p.code).filter(c => has(r, c)) })
    }
    ElMessage.success('已保存，持有这些角色的人下次操作时生效')
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

// ---- 新建 / 重命名 ----
const editing = ref<RoleRow | null>(null)
const open = ref(false)
const name = ref('')
function openCreate() { editing.value = null; name.value = ''; open.value = true }
function openRename(row: RoleRow) { editing.value = row; name.value = row.name; open.value = true }
async function saveName() {
  const value = name.value.trim()
  if (!value) { ElMessage.error('请输入角色名称'); return }
  saving.value = true
  try {
    if (editing.value) await adminRolesApi.update(editing.value.id, { name: value, permissions: editing.value.permissions })
    else await adminRolesApi.create({ name: value, permissions: [] })
    ElMessage.success(editing.value ? '已重命名' : '角色已创建，请在表格里勾选它的功能')
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
  <div class="settings-page roles-page">
    <div class="head-row">
      <div><h2>角色</h2><p class="aux">勾选每个角色能用的功能。改动过的格子高亮，保存后生效。任何角色都不能查看他人的审核与会话内容。</p></div>
      <el-button type="primary" @click="openCreate"><template #icon><Plus :size="16" :stroke-width="1.75" /></template>新建角色</el-button>
    </div>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" class="gap" />
    <div v-if="!loaded" class="skel" aria-busy="true" />
    <div v-else v-loading="loading" class="card mx-wrap">
      <table class="matrix">
        <thead>
          <tr>
            <th scope="col">功能</th>
            <th v-for="r in roles" :key="r.id" scope="col">
              <span class="rn">{{ r.name }}</span>
              <span class="rs num">{{ r.users }} 人<template v-if="r.builtin"> · {{ BUILTIN_NOTE[r.builtin] }}</template></span>
              <RowMore class="rm"><el-dropdown-item @click="openRename(r)">重命名</el-dropdown-item><el-dropdown-item v-if="!r.builtin" class="danger-item" @click="remove(r)">删除</el-dropdown-item></RowMore>
            </th>
          </tr>
        </thead>
        <tbody v-for="g in groups" :key="g.name">
          <tr class="g" :style="{ '--mc': GROUP_COLOR[g.name] ?? 'var(--si-ink-3)' }"><td :colspan="roles.length + 1">{{ g.name }}</td></tr>
          <tr v-for="p in g.items" :key="p.code">
            <td>{{ p.name }}</td>
            <td v-for="r in roles" :key="r.id" :class="{ chg: changed(r, p.code) }">
              <button type="button" role="checkbox" class="cb2" :class="{ on: has(r, p.code) }" :aria-checked="has(r, p.code)" :aria-label="`${r.name} · ${p.name}`" :disabled="locked(r, p.code) || saving" :title="locked(r, p.code) ? '必须保留，否则没有人能再管理账号' : undefined" @click="toggle(r, p.code)"><Check v-if="has(r, p.code)" :size="14" :stroke-width="3" /></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="changeCount" class="savebar" role="status"><span>{{ changeCount }} 处改动</span><el-button class="drop" size="small" :disabled="saving" @click="resetDraft">放弃</el-button><el-button type="primary" size="small" :loading="saving" @click="saveAll">保存</el-button></div>

    <el-dialog v-model="open" :title="editing ? '重命名角色' : '新建角色'" width="480px" :close-on-click-modal="false">
      <el-form label-position="left" label-width="140px" @submit.prevent="saveName">
        <el-form-item label="名称"><el-input v-model="name" maxlength="30" show-word-limit /></el-form-item>
      </el-form>
      <template #footer><el-button @click="open = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveName">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.head-row h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.aux { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.card { background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.mx-wrap { padding: 4px 0 8px; overflow-x: auto; }
.skel { height: 240px; border-radius: var(--si-radius-card); background: var(--si-hover-bg); }
.matrix { border-collapse: separate; border-spacing: 0; width: 100%; font: var(--si-font-body); }
.matrix th { position: relative; padding: 12px 8px; text-align: center; font-weight: 500; vertical-align: top; background: var(--si-surface-1); }
.matrix th:first-child, .matrix td:first-child { padding-left: 18px; text-align: left; }
.rn { display: block; padding: 0 22px; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.rs { display: block; margin-top: 2px; color: var(--si-ink-3); font: var(--si-font-caption); }
.rm { position: absolute; top: 8px; right: 2px; }
.matrix th:first-child { color: var(--si-ink-3); font: var(--si-font-meta); }
.matrix td { height: 44px; padding: 0 8px; border-top: 1px solid var(--si-hair-faint); text-align: center; color: var(--si-ink-2); }
.matrix tr.g td { height: 36px; background: var(--si-card-2); color: var(--si-ink-1); font-weight: 600; text-align: left; box-shadow: inset 3px 0 0 var(--mc); }
.matrix td.chg { background: var(--si-warn-soft); }
.cb2 { display: inline-grid; place-items: center; width: 20px; height: 20px; padding: 0; border: 1.5px solid var(--si-hair-strong); border-radius: 6px; background: var(--si-surface-1); color: var(--si-white); cursor: pointer; transition: background var(--si-d2) var(--si-ease-out); }
.cb2.on { background: var(--si-brand-2); border-color: var(--si-brand-2); }
.cb2:disabled { cursor: not-allowed; opacity: .65; }
.cb2:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.savebar { position: sticky; bottom: 16px; z-index: 5; display: flex; align-items: center; gap: 12px; margin-top: 16px; padding: 10px 12px 10px 18px; border-radius: var(--si-radius-card); background: var(--si-island-bg); color: var(--si-island-ink); box-shadow: var(--si-shadow-2); font: var(--si-font-label); }
.savebar .drop { margin-left: auto; background: transparent; color: inherit; border-color: color-mix(in srgb, var(--si-white) 25%, transparent); }
</style>
