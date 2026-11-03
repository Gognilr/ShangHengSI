<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Bell, ChevronsUpDown, FileSearch, HistoryIcon, MessageSquare, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings, Sun } from '@lucide/vue'
import AlertList from '@/components/AlertList.vue'
import AppearanceDialog from '@/components/AppearanceDialog.vue'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'
import SidebarSessions from '@/components/SidebarSessions.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import SiLogo from '@/components/brand/SiLogo.vue'
import { ApiError } from '@/api/http'
import { useAppearanceStore } from '@/stores/appearance'
import { useAuthStore } from '@/stores/auth'
import { useSessionsStore } from '@/stores/sessions'

const auth = useAuthStore()
const appearance = useAppearanceStore()
// 外壳一键切换（V31Q-95）：与“设置 · 个人 · 外观”同一份设置
const shellNext = computed(() => appearance.shell === 'night' ? '浅色' : '夜幕')
function toggleShell() { appearance.shell = appearance.shell === 'night' ? 'light' : 'night' }
const sessions = useSessionsStore()
const route = useRoute()
const router = useRouter()

const appearanceOpen = ref(false)
const alertsOpen = ref(false)
const pwdOpen = ref(false)
// 须改密期间对话框始终打开且不可关闭
const pwdVisible = computed(() => pwdOpen.value || auth.mustChangePassword)

// 侧栏宽度与收起状态只存本浏览器（V3-A 〇.2）
const SIDEBAR_MIN = 200, SIDEBAR_MAX = 320, SIDEBAR_DEFAULT = 232
const width = ref(readStoredWidth('si.sidebar.width', SIDEBAR_MIN, SIDEBAR_MAX, SIDEBAR_DEFAULT))
const collapsed = ref(readCollapsed())
function readCollapsed() { try { return localStorage.getItem('si.sidebar.collapsed') === '1' } catch { return false } }
function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try { localStorage.setItem('si.sidebar.collapsed', collapsed.value ? '1' : '0') } catch { /* 只影响下次打开 */ }
}

const menus = [
  { path: '/assistant', label: '智能助手', icon: MessageSquare },
  { path: '/review/new', label: '智能审核', icon: FileSearch },
  { path: '/history', label: '审核历史', icon: HistoryIcon },
]
// 选中规则沿用原顶栏：/review/:id 归“智能审核”，设置页不选中菜单。
const activeMenu = computed(() => route.path.startsWith('/settings') ? '' : route.path.startsWith('/assistant') ? '/assistant' : (route.path.startsWith('/history') ? '/history' : '/review/new'))
const displayName = computed(() => auth.user?.display_name ?? '')
const initial = computed(() => Array.from(displayName.value)[0] ?? '')

// 全局搜索：侧栏按钮或 Ctrl+K（Mac 为 ⌘K）打开；须改密期间不响应
const searchOpen = ref(false)
function onShortcut(event: KeyboardEvent) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'k') return
  event.preventDefault()
  if (!auth.mustChangePassword) searchOpen.value = true
}
onMounted(() => window.addEventListener('keydown', onShortcut))
onBeforeUnmount(() => window.removeEventListener('keydown', onShortcut))

const creating = ref(false)
async function createSession() {
  if (creating.value) return
  creating.value = true
  // V31-U5：回到助手首页，第一次发送或加入文件时才建会话
  try { await router.push('/assistant') }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '新建会话失败，请稍后重试') }
  finally { creating.value = false }
}

// 换了登录用户或改完初始密码后重新取近期会话
watch(() => [auth.user?.id, auth.mustChangePassword] as const, ([id, mustChange]) => {
  sessions.clear()
  if (id && !mustChange) void sessions.refresh()
}, { immediate: true })

async function onCommand(cmd: string) {
  if (cmd === 'appearance') {
    appearanceOpen.value = true
  } else if (cmd === 'password') {
    pwdOpen.value = true
  } else if (cmd === 'logout') {
    await auth.logout()
    sessions.clear()
    await router.replace({ name: 'login' })
  }
}
</script>

<template>
  <div class="layout" :class="{ collapsed }">
    <aside class="sidebar" :style="{ width: `${collapsed ? 64 : width}px` }" aria-label="主导航">
      <div class="brand"><SiLogo :mark-only="collapsed" /></div>

      <div class="block">
        <el-tooltip content="新建会话" placement="right" :disabled="!collapsed">
          <button type="button" class="new-session" :disabled="creating || auth.mustChangePassword" :aria-label="collapsed ? '新建会话' : undefined" @click="createSession">
            <Plus :size="16" :stroke-width="1.75" /><span class="label">新建会话</span>
          </button>
        </el-tooltip>
      </div>

      <nav class="block menu" aria-label="菜单">
        <el-tooltip v-for="m in menus" :key="m.path" :content="m.label" placement="right" :disabled="!collapsed">
          <router-link :to="m.path" class="item" :class="{ selected: activeMenu === m.path }" :aria-current="activeMenu === m.path ? 'page' : undefined" :aria-label="collapsed ? m.label : undefined">
            <component :is="m.icon" :size="16" :stroke-width="1.75" /><span class="label">{{ m.label }}</span>
          </router-link>
        </el-tooltip>
        <el-tooltip content="搜索（Ctrl K）" placement="right" :disabled="!collapsed">
          <button type="button" class="item" :disabled="auth.mustChangePassword" :aria-label="collapsed ? '搜索' : undefined" aria-keyshortcuts="Control+K" @click="searchOpen = true">
            <Search :size="16" :stroke-width="1.75" /><span class="label">搜索</span><kbd class="label">Ctrl K</kbd>
          </button>
        </el-tooltip>
      </nav>

      <div class="recent">
        <SidebarSessions v-if="!collapsed && !auth.mustChangePassword" />
      </div>

      <div class="block bottom">
        <!-- 管理员提醒（SPEC 6.7）：侧栏一行，点开弹层逐条处理，不占主区域 -->
        <el-popover v-if="auth.isAdmin && auth.visibleAlerts.length" v-model:visible="alertsOpen" trigger="click" placement="right-end" :width="340" :show-arrow="false" :offset="12">
          <template #reference>
            <button type="button" class="item alert-item" :aria-label="`提醒 ${auth.visibleAlerts.length} 条`" :aria-expanded="alertsOpen">
              <span class="bell"><Bell :size="16" :stroke-width="1.75" /><span class="bell-dot" aria-hidden="true" /></span><span class="label">提醒</span><span class="label alert-count num">{{ auth.visibleAlerts.length }}</span>
            </button>
          </template>
          <AlertList @navigate="alertsOpen = false" />
        </el-popover>
        <el-tooltip content="设置" placement="right" :disabled="!collapsed">
          <router-link to="/settings" class="item" :class="{ selected: route.path.startsWith('/settings') }" :aria-current="route.path.startsWith('/settings') ? 'page' : undefined" :aria-label="collapsed ? '设置' : undefined">
            <Settings :size="16" :stroke-width="1.75" /><span class="label">设置</span>
          </router-link>
        </el-tooltip>
        <div class="user-row">
          <el-dropdown trigger="click" placement="top-start" @command="onCommand">
            <button type="button" class="user" :aria-label="`${displayName}，账号菜单`">
              <span class="avatar">{{ initial }}</span><span class="label display-name">{{ displayName }}</span><ChevronsUpDown class="label" :size="16" :stroke-width="1.75" />
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="appearance">外观</el-dropdown-item>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-tooltip :content="`切换到${shellNext}`" placement="top">
            <button type="button" class="toggle" :aria-label="`切换到${shellNext}外壳`" @click="toggleShell">
              <Sun v-if="appearance.shell === 'night'" :size="16" :stroke-width="1.75" /><Moon v-else :size="16" :stroke-width="1.75" />
            </button>
          </el-tooltip>
          <el-tooltip :content="collapsed ? '展开侧栏' : '收起侧栏'" placement="right">
            <button type="button" class="toggle" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" :aria-expanded="!collapsed" @click="toggleCollapsed">
              <PanelLeftOpen v-if="collapsed" :size="16" :stroke-width="1.75" /><PanelLeftClose v-else :size="16" :stroke-width="1.75" />
            </button>
          </el-tooltip>
        </div>
      </div>

      <SplitHandle v-if="!collapsed" v-model="width" class="resize" :min="SIDEBAR_MIN" :max="SIDEBAR_MAX" :default-value="SIDEBAR_DEFAULT" storage-key="si.sidebar.width" label="调整侧栏宽度" />
    </aside>

    <div class="content">
      <main class="main">
        <p v-if="auth.mustChangePassword" class="must-change">请先修改初始密码</p>
        <router-view v-else v-slot="{ Component }">
          <Transition name="si-page"><component :is="Component" /></Transition>
        </router-view>
      </main>
    </div>

    <GlobalSearch v-model="searchOpen" @new-session="createSession" />
    <AppearanceDialog v-model="appearanceOpen" />
    <ChangePasswordDialog
      :model-value="pwdVisible"
      :forced="auth.mustChangePassword"
      @update:model-value="pwdOpen = $event"
    />
  </div>
</template>

<style scoped>
.layout {
  /* 整体固定为视口高度，内容在 .main 内滚动：外层不出现滚动条，避免挤占 body 的 1280px 最小宽度 */
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  height: 100vh;
}

.sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  box-sizing: border-box;
  background: var(--si-bg-side);
  border-right: 1px solid var(--si-hair-faint);
}
.resize { position: absolute; top: 0; bottom: 0; right: -4px; z-index: 2; }

.brand { flex: none; display: flex; align-items: center; height: 56px; padding: 0 20px; }
.collapsed .brand { justify-content: center; padding: 0; }
.block { flex: none; padding: 4px 8px; }
.menu { display: flex; flex-direction: column; gap: 2px; padding-bottom: 12px; border-bottom: 1px solid var(--si-hair-faint); }
.recent { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 0; }
.bottom { display: flex; flex-direction: column; gap: 2px; padding-top: 8px; padding-bottom: 12px; border-top: 1px solid var(--si-hair-faint); }

.item, .new-session {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  box-sizing: border-box;
  border: 0;
  border-radius: var(--el-border-radius-base);
  background: transparent;
  color: var(--si-ink-2);
  font: var(--si-font-label);
  text-decoration: none;
  cursor: pointer;
  transition: background-color var(--si-dur-1) var(--si-ease), color var(--si-dur-1) var(--si-ease), transform var(--si-dur-1) var(--si-ease);
}
.item svg, .new-session svg { flex: none; }
.item:hover { background: var(--si-surface-2); color: var(--si-ink-1); }
/* 选中：蓝色渐变浅底 + 左侧 3px 竖条（完整特效带辉光） */
.item.selected { background: linear-gradient(90deg, color-mix(in srgb, var(--si-blue) 20%, transparent), color-mix(in srgb, var(--si-blue) 6%, transparent)); color: var(--si-ink-1); }
.item.selected svg { color: var(--si-blue-ink); }
.item.selected::before { content: ""; position: absolute; left: -8px; top: 8px; bottom: 8px; width: 3px; border-radius: 0 3px 3px 0; background: var(--si-blue); box-shadow: var(--si-glow-blue); }
.item:disabled, .new-session:disabled { cursor: not-allowed; opacity: .6; }
.item:active:not(:disabled), .new-session:active:not(:disabled) { transform: scale(.97); }
.new-session { justify-content: center; border: 1px solid var(--si-hair-strong); background: color-mix(in srgb, var(--si-ink-1) 8%, transparent); color: var(--si-ink-1); }
.new-session:hover:not(:disabled) { background: color-mix(in srgb, var(--si-ink-1) 14%, transparent); }
/* 提醒：铃铛右上角琥珀色点，计数靠右；收起时只剩带点的铃铛 */
.bell { position: relative; display: grid; place-items: center; }
.bell-dot { position: absolute; top: -2px; right: -3px; width: 7px; height: 7px; border-radius: 50%; background: var(--si-warn); box-shadow: 0 0 0 2px var(--si-bg-side); }
.alert-count { margin-left: auto; min-width: 20px; height: 20px; padding: 0 6px; box-sizing: border-box; border-radius: 10px; background: color-mix(in srgb, var(--si-warn) 18%, transparent); color: var(--si-warn-ink); font: var(--si-font-caption); line-height: 20px; text-align: center; }
kbd { margin-left: auto; font: var(--si-font-caption); color: var(--si-ink-4); }

.user-row { display: flex; align-items: center; gap: 4px; }
.user-row :deep(.el-dropdown) { flex: 1; min-width: 0; }
.user { display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; height: 40px; padding: 0 8px; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--si-ink-2); font: var(--si-font-body); cursor: pointer; transition: background-color var(--si-dur-1) var(--si-ease); }
.user:hover { background: var(--si-surface-2); }
.avatar { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: color-mix(in srgb, var(--si-blue) 16%, var(--si-bg-side)); color: var(--si-blue-ink); font-weight: 600; }
.display-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
.user svg { flex: none; color: var(--si-ink-3); }
.toggle { flex: none; display: grid; place-items: center; width: 32px; height: 32px; padding: 0; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--si-ink-3); cursor: pointer; transition: background-color var(--si-dur-1) var(--si-ease), color var(--si-dur-1) var(--si-ease); }
.toggle:hover { background: var(--si-surface-2); color: var(--si-ink-1); }

/* 收起为 64px 窄栏：只留图标，文字由悬停提示与 aria-label 提供 */
.collapsed .label { display: none; }
.collapsed .item, .collapsed .new-session { justify-content: center; padding: 0; }
.collapsed .user-row { flex-direction: column; }
.collapsed .user { justify-content: center; padding: 0; }

.content { display: flex; flex-direction: column; min-width: 0; min-height: 0; }

.main {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* 完整特效下叠氛围光：背景属于 .main 自身，不随内容滚动，不动画 */
:global(html[data-fx="full"]) .main { background-image: var(--si-ambient); background-repeat: no-repeat; }
.main :deep(.confirm-actions) { bottom: 0; }
.must-change { margin: 0; padding: 48px 32px; text-align: center; color: var(--si-ink-3); }
</style>
