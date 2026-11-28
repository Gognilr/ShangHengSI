<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ClockFading, FileSearch, FileStack, GitCompareArrows, House, Info, KeyRound, Languages, LogOut, Mic, Moon, Palette, PanelLeftClose, PanelLeftOpen, Plus, ReceiptText, Search, Settings, Sun } from '@lucide/vue'
import AboutDialog from '@/components/AboutDialog.vue'
import AlertList from '@/components/AlertList.vue'
import AppearanceDialog from '@/components/AppearanceDialog.vue'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import GlobalSearch from '@/components/GlobalSearch.vue'
import LoginNotice from '@/components/LoginNotice.vue'
import type { Perm } from '@/utils/permissions'
import SidebarSessions from '@/components/SidebarSessions.vue'
import SplitHandle, { readStoredWidth } from '@/components/SplitHandle.vue'
import SiLogo from '@/components/brand/SiLogo.vue'
import { assistantApi, type Waiting } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { useAppearanceStore } from '@/stores/appearance'
import { useAuthStore } from '@/stores/auth'
import { useSessionsStore } from '@/stores/sessions'

const auth = useAuthStore()
const appearance = useAppearanceStore()
// 外壳一键切换（V31Q-95）：与“设置 · 个人 · 外观”同一份设置
const shellNext = computed(() => appearance.shell === 'night' ? '浅色' : '深色')
function toggleShell() { appearance.themeChoice = appearance.shell === 'night' ? 'light' : 'night' }
const sessions = useSessionsStore()
const route = useRoute()
const router = useRouter()

const appearanceOpen = ref(false)
const pwdOpen = ref(false)
const aboutOpen = ref(false)

// 侧栏宽度与收起状态只存本浏览器（V3-A 〇.2）；澄明 8.0：默认 248，收起为 68，窗口窄于 1280 时自动收起
const SIDEBAR_MIN = 220, SIDEBAR_MAX = 320, SIDEBAR_DEFAULT = 248, SIDEBAR_FOLDED = 68
const width = ref(readStoredWidth('si.sidebar.width', SIDEBAR_MIN, SIDEBAR_MAX, SIDEBAR_DEFAULT))
const folded = ref(readCollapsed())
const narrow = ref(false)
const collapsed = computed(() => folded.value || narrow.value)
function readCollapsed() { try { return localStorage.getItem('si.sidebar.collapsed') === '1' } catch { return false } }
function toggleCollapsed() {
  // 窄窗口里自动收起时点“展开”：只在本次临时展开，不改存下的选择
  if (narrow.value) { narrow.value = false; folded.value = false; return }
  folded.value = !folded.value
  try { localStorage.setItem('si.sidebar.collapsed', folded.value ? '1' : '0') } catch { /* 只影响下次打开 */ }
}
const narrowQuery = window.matchMedia('(max-width: 1279px)')
function onNarrow() { narrow.value = narrowQuery.matches }
onMounted(() => { onNarrow(); narrowQuery.addEventListener('change', onNarrow) })
onBeforeUnmount(() => narrowQuery.removeEventListener('change', onNarrow))

// 菜单按权限点显示（V31-U5F-07）；翻译可只用本地翻译、历史记录是本人的记录，人人可见
// V3.4 一事一入口（V34Q-01）：工作台｜文件审查｜工具｜历史记录、搜索；分组名在收起的窄栏里变为一条短线
// 合同对比（V351-E-01）放在文件审核之后，同属审核权限与审核模块色；它与发票管家（V36-I）都不留记录，没有等待数
// 澄明 8.0：选中时图标变成模块色（mc），工作台、历史记录、搜索没有模块色，用衡青
type Menu = { key: keyof Waiting | 'history' | 'compare' | 'invoice'; to: string; label: string; icon: typeof House; mc?: string; perms?: Perm[] }
const SECTIONS: { title: string; items: Menu[] }[] = [
  { title: '', items: [{ key: 'workbench', to: '/assistant', label: '工作台', icon: House, perms: ['use.assistant'] }] },
  { title: '文件审查', items: [
    { key: 'review', to: '/review', label: '文件审核', icon: FileSearch, mc: 'review', perms: ['use.review'] },
    { key: 'compare', to: '/compare', label: '合同对比', icon: GitCompareArrows, mc: 'review', perms: ['use.review'] },
    { key: 'bid', to: '/bid', label: '标书初审', icon: FileStack, mc: 'bid', perms: ['use.bid'] },
  ] },
  { title: '工具', items: [
    { key: 'meeting', to: '/meeting', label: '会议纪要', icon: Mic, mc: 'meet', perms: ['use.meeting'] },
    { key: 'translate', to: '/translate', label: '翻译', icon: Languages, mc: 'trans' },
    { key: 'invoice', to: '/invoice', label: '发票管家', icon: ReceiptText, mc: 'inv', perms: ['use.export'] },
  ] },
  { title: '', items: [{ key: 'history', to: '/history', label: '历史记录', icon: ClockFading }] },
]
const sections = computed(() => SECTIONS.map(s => ({ ...s, items: s.items.filter(m => !m.perms || auth.can(...m.perms)) })).filter(s => s.items.length))
// 选中按“这件事属于哪一类”（V34Q-01）：/review、旧审核任务页、发起过审核或打开过审核视图的会话 → 文件审核；
// 其余工作台会话 → 工作台。会话内“审核结果 / 提问”是页内页签，打开过审核视图后不再随切换跳回。设置页、不存在页不选中菜单。
const activeMenu = computed(() => {
  const path = route.path
  if (path === '/review' || path.startsWith('/review/')) return 'review'
  if (path === '/compare') return 'compare'
  if (path === '/bid' || path.startsWith('/bid/')) return 'bid'
  if (path.startsWith('/assistant')) {
    const sid = typeof route.params.sessionId === 'string' ? route.params.sessionId : ''
    return sid && auth.can('use.review') && (!auth.can('use.assistant') || route.query.mode === 'review' || sessions.isReview(sid)) ? 'review' : 'workbench'
  }
  if (path.startsWith('/meeting')) return 'meeting'
  if (path.startsWith('/translate')) return 'translate'
  if (path === '/invoice') return 'invoice'
  if (path.startsWith('/history')) return 'history'
  return ''
})
const displayName = computed(() => auth.user?.display_name ?? '')
const initial = computed(() => Array.from(displayName.value)[0] ?? '')
// 账号行第二行：没有部门字段，写所兼角色
const hasAlerts = computed(() => auth.isAdmin && auth.visibleAlerts.length > 0)
const roleLine = computed(() => (auth.user?.roles ?? []).join('、'))

// 菜单右侧的数字只数等你处理的（澄明 8.0）：进页面、换页和每分钟（页面在前台时）各取一次
const waiting = ref<Waiting | null>(null)
async function refreshWaiting() {
  if (!auth.user) { waiting.value = null; return }
  try { waiting.value = await assistantApi.waiting() } catch { /* 数字取不到时不显示，不打断页面 */ }
}
function badge(m: Menu) { const n = m.key === 'history' || m.key === 'compare' || m.key === 'invoice' ? 0 : waiting.value?.[m.key] ?? 0; return n > 99 ? '99+' : n || '' }
const BADGE_TIP: Record<string, string> = { workbench: '等你确认遮住的会话', review: '等你确认遮住的文件', bid: '等你确认后发送的标书项目', meeting: '等你确认遮住的会议', translate: '等你确认遮住的译稿' }
let waitTimer: number | undefined
watch(() => route.path, () => void refreshWaiting())
// 近期会话：别处（另一台电脑）的增删改靠这里同步——每分钟看一次，切回本页时立即看一次
function syncSessions() { if (!document.hidden && auth.can('use.assistant', 'use.review')) void sessions.refresh() }
function onVisible() { if (!document.hidden) { syncSessions(); void refreshWaiting() } }
onMounted(() => { waitTimer = window.setInterval(() => { if (!document.hidden) { void refreshWaiting(); syncSessions() } }, 60000); document.addEventListener('visibilitychange', onVisible); window.addEventListener('focus', onVisible) })
onBeforeUnmount(() => { window.clearInterval(waitTimer); document.removeEventListener('visibilitychange', onVisible); window.removeEventListener('focus', onVisible) })

// 全局搜索：侧栏按钮或 Ctrl+K（Mac 为 ⌘K）打开
const searchOpen = ref(false)
function onShortcut(event: KeyboardEvent) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'k') return
  event.preventDefault()
  searchOpen.value = true
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

// 换了登录用户后重新取近期会话（须改密的会话停在登录卡第二步，进不了主框架，见路由守卫）
watch(() => auth.user?.id, id => {
  sessions.clear()
  if (id && auth.can('use.assistant', 'use.review')) void sessions.refresh()
  void refreshWaiting()
}, { immediate: true })

const inSettings = computed(() => route.path.startsWith('/settings'))
const accountOpen = ref(false)
async function onCommand(cmd: string) {
  accountOpen.value = false
  if (cmd === 'settings') {
    await router.push('/settings')
  } else if (cmd === 'appearance') {
    appearanceOpen.value = true
  } else if (cmd === 'about') {
    aboutOpen.value = true
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
    <aside class="sidebar" :style="{ width: `${collapsed ? SIDEBAR_FOLDED : width}px` }" aria-label="主导航">
      <div class="brand">
        <SiLogo :mark-only="collapsed" />
        <el-tooltip v-if="!collapsed" content="收起侧栏" placement="right"><button type="button" class="toggle collapse" aria-label="收起侧栏" aria-expanded="true" @click="toggleCollapsed"><PanelLeftClose :size="18" :stroke-width="1.75" /></button></el-tooltip>
      </div>

      <div v-if="auth.can('use.assistant')" class="block">
        <el-tooltip content="新建会话" placement="right" :disabled="!collapsed">
          <button type="button" class="new-session" :disabled="creating" :aria-label="collapsed ? '新建会话' : undefined" @click="createSession">
            <Plus :size="18" :stroke-width="2" /><span class="label">新建会话</span>
          </button>
        </el-tooltip>
      </div>

      <nav class="block menu" aria-label="菜单">
        <template v-for="(s, i) in sections" :key="s.title || i">
          <div v-if="s.title" class="group" role="presentation"><span class="label">{{ s.title }}</span></div>
          <div v-else-if="i > 0" class="rule" role="presentation" />
          <el-tooltip v-for="m in s.items" :key="m.key" :content="badge(m) ? `${m.label} · ${BADGE_TIP[m.key]} ${badge(m)}` : m.label" placement="right" :disabled="!collapsed">
            <router-link :to="m.to" class="item" :class="[{ selected: activeMenu === m.key }, m.mc ? `m-${m.mc}` : '']" :aria-current="activeMenu === m.key ? 'page' : undefined" :aria-label="collapsed ? m.label : undefined">
              <component :is="m.icon" class="icon" :size="18" :stroke-width="1.75" /><span class="label">{{ m.label }}</span>
              <span v-if="badge(m)" class="count num" :title="BADGE_TIP[m.key]" :aria-label="`${BADGE_TIP[m.key]} ${badge(m)}`">{{ badge(m) }}</span>
            </router-link>
          </el-tooltip>
        </template>
        <el-tooltip content="搜索（Ctrl K）" placement="right" :disabled="!collapsed">
          <button type="button" class="item" :aria-label="collapsed ? '搜索' : undefined" aria-keyshortcuts="Control+K" @click="searchOpen = true">
            <Search class="icon" :size="18" :stroke-width="1.75" /><span class="label">搜索</span><kbd class="label si-kbd">Ctrl K</kbd>
          </button>
        </el-tooltip>
      </nav>

      <div class="recent">
        <SidebarSessions v-if="!collapsed && auth.can('use.assistant', 'use.review')" />
      </div>

      <div class="account">
        <!-- 账号菜单（澄明 8.0）：设置、外观、关于、修改密码、退出；管理员提醒收在菜单顶部，头像上的琥珀色圆点表示有提醒（SPEC 6.7） -->
        <el-popover v-model:visible="accountOpen" trigger="click" placement="top-start" :width="300" :show-arrow="false" :offset="8" popper-class="account-pop">
          <template #reference>
            <button type="button" class="user" :class="{ selected: inSettings }" :aria-label="`${displayName}，账号菜单${hasAlerts ? `，${auth.visibleAlerts.length} 条管理提醒` : ''}`" :aria-expanded="accountOpen">
              <span class="avatar">{{ initial }}<i v-if="hasAlerts" class="alert-dot" aria-hidden="true" /></span>
              <span class="label who"><b>{{ displayName }}</b><span v-if="roleLine">{{ roleLine }}</span></span>
            </button>
          </template>
          <div class="account-menu">
            <AlertList v-if="hasAlerts" class="alerts" @navigate="accountOpen = false" />
            <button type="button" class="menu-item" @click="onCommand('settings')"><Settings :size="16" :stroke-width="1.75" />设置</button>
            <button type="button" class="menu-item" @click="onCommand('appearance')"><Palette :size="16" :stroke-width="1.75" />外观</button>
            <button type="button" class="menu-item" @click="onCommand('about')"><Info :size="16" :stroke-width="1.75" />关于</button>
            <button type="button" class="menu-item" @click="onCommand('password')"><KeyRound :size="16" :stroke-width="1.75" />修改密码</button>
            <div class="menu-rule" role="separator" />
            <button type="button" class="menu-item" @click="onCommand('logout')"><LogOut :size="16" :stroke-width="1.75" />退出</button>
          </div>
        </el-popover>
        <el-tooltip :content="`切换到${shellNext}`" placement="top">
          <button type="button" class="toggle" :aria-label="`切换到${shellNext}主题`" @click="toggleShell">
            <Sun v-if="appearance.shell === 'night'" :size="18" :stroke-width="1.75" /><Moon v-else :size="18" :stroke-width="1.75" />
          </button>
        </el-tooltip>
        <el-tooltip v-if="collapsed" content="展开侧栏" placement="right">
          <button type="button" class="toggle" aria-label="展开侧栏" aria-expanded="false" @click="toggleCollapsed"><PanelLeftOpen :size="18" :stroke-width="1.75" /></button>
        </el-tooltip>
      </div>

      <SplitHandle v-if="!collapsed" v-model="width" class="resize" :min="SIDEBAR_MIN" :max="SIDEBAR_MAX" :default-value="SIDEBAR_DEFAULT" storage-key="si.sidebar.width" label="调整侧栏宽度" />
    </aside>

    <div class="content">
      <LoginNotice />
      <main class="main">
        <router-view v-slot="{ Component }">
          <Transition name="si-page"><component :is="Component" /></Transition>
        </router-view>
      </main>
    </div>

    <GlobalSearch v-model="searchOpen" @new-session="createSession" />
    <AppearanceDialog v-model="appearanceOpen" />
    <ChangePasswordDialog v-model="pwdOpen" />
    <AboutDialog v-model="aboutOpen" />
  </div>
</template>

<style scoped>
.layout {
  /* 整体固定为视口高度，内容在 .main 内滚动：外层不出现滚动条，避免挤占 body 的 1280px 最小宽度 */
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  height: 100vh;
}

/* 侧栏（澄明 8.0）：白底细线，自上而下为标志、新建会话、菜单、近期会话、账号行 */
.sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  box-sizing: border-box;
  padding: 16px 12px 12px;
  background: var(--si-bg-side);
  border-right: 1px solid var(--si-hair);
}
.resize { position: absolute; top: 0; bottom: 0; right: -4px; z-index: 2; }

.brand { flex: none; display: flex; align-items: center; height: 40px; padding: 0 0 0 10px; margin-bottom: 12px; }
.collapse { margin-left: auto; }
.collapsed .brand { justify-content: center; padding: 0; }
.block { flex: none; }
.menu { display: flex; flex-direction: column; gap: 2px; }
.group { padding: 14px 12px 6px; font: var(--si-font-caption); font-weight: 500; color: var(--si-ink-4); }
.rule { height: 1px; margin: 10px 12px; background: var(--si-hair-faint); }
.collapsed .group { padding: 0; height: 1px; margin: 10px 14px; background: var(--si-hair-faint); }
.recent { flex: 1; min-height: 0; overflow-y: auto; margin: 4px -12px 0; padding: 0 12px; }

.new-session {
  display: flex; align-items: center; gap: 10px; width: 100%; height: 44px; margin-bottom: 14px; padding: 0 14px; box-sizing: border-box;
  border: 0; border-radius: var(--si-radius-md); background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-label); font-weight: 600;
  cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out), transform var(--si-d1) var(--si-ease-out);
}
.new-session:hover:not(:disabled) { background: color-mix(in srgb, var(--si-blue-tint) 70%, var(--si-brand-line)); }

.item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  box-sizing: border-box;
  border: 0;
  border-radius: var(--si-radius-control);
  background: transparent;
  color: var(--si-ink-2);
  font: var(--si-font-label);
  text-decoration: none;
  cursor: pointer;
  transition: background-color var(--si-d2) var(--si-ease-out), color var(--si-d2) var(--si-ease-out), transform var(--si-d1) var(--si-ease-out);
}
.item svg, .new-session svg { flex: none; }
.item .icon { color: var(--si-ink-3); transition: color var(--si-d2) var(--si-ease-out), transform var(--si-d3) var(--si-ease-spring); }
.item:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
html[data-fx="full"] .item:hover .icon { transform: scale(1.08); }
/* 选中：选中底色、字重 600，图标变成模块色；没有模块色的用衡青 */
.item.selected { background: var(--si-select-bg); color: var(--si-ink-1); font-weight: 600; }
.item.selected .icon { color: var(--mc, var(--si-brand-2)); }
.m-review { --mc: var(--si-m-review); }
.m-bid { --mc: var(--si-m-bid); }
.m-meet { --mc: var(--si-m-meet); }
.m-trans { --mc: var(--si-m-trans); }
.m-inv { --mc: var(--si-m-inv); }
.item:disabled, .new-session:disabled { cursor: not-allowed; opacity: .6; }
.item:active:not(:disabled), .new-session:active:not(:disabled) { transform: scale(.97); }
/* 等你处理的数量：琥珀浅底胶囊；收起时变成图标右上角的小圆点 */
.count { margin-left: auto; min-width: 22px; height: 20px; padding: 0 7px; box-sizing: border-box; border-radius: var(--si-radius-badge); background: var(--si-warn-soft); color: var(--si-warn-ink); font: var(--si-font-badge); font-weight: 600; line-height: 20px; text-align: center; }
.item .si-kbd { margin-left: auto; }

.account { flex: none; display: flex; align-items: center; gap: 4px; margin-top: 8px; padding-top: 10px; border-top: 1px solid var(--si-hair-faint); }
.user { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; padding: 6px 8px; border: 0; border-radius: var(--si-radius-control); background: transparent; color: var(--si-ink-2); text-align: left; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out); }
.user:hover, .user[aria-expanded="true"] { background: var(--si-hover-bg); }
.user.selected { background: var(--si-select-bg); }
.avatar { position: relative; flex: none; display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%; background: var(--si-avatar); color: var(--si-white); font: var(--si-font-label); font-weight: 600; }
.alert-dot { position: absolute; top: -1px; right: -1px; width: 10px; height: 10px; box-sizing: border-box; border-radius: 50%; background: var(--si-warn); border: 2px solid var(--si-bg-side); }
.who { display: flex; flex-direction: column; min-width: 0; }
.who b, .who span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who b { font: var(--si-font-label); font-weight: 600; color: var(--si-ink-1); }
.who span { font: var(--si-font-meta); color: var(--si-ink-3); }
.toggle { flex: none; display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 0; border-radius: var(--si-radius-control); background: transparent; color: var(--si-ink-3); cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out), color var(--si-d2) var(--si-ease-out); }
.toggle:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }

/* 收起为 68px 窄栏：只留图标，文字由悬停提示与 aria-label 提供 */
.collapsed .label { display: none; }
.collapsed .item, .collapsed .new-session { justify-content: center; padding: 0; }
.collapsed .count { position: absolute; top: 7px; right: 9px; min-width: 0; width: 8px; height: 8px; padding: 0; overflow: hidden; color: transparent; background: var(--si-warn); }
.collapsed .account { flex-direction: column; }
.collapsed .user { flex: none; justify-content: center; padding: 4px; }

.content { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.main { flex: 1; min-height: 0; overflow-y: auto; background: var(--si-bg-canvas); }
.main :deep(.confirm-actions) { bottom: 0; }
</style>

<style>
/* 账号菜单弹层（teleport 到 body，不加 scoped） */
.account-pop.el-popover.el-popper { padding: 6px; }
.account-menu { display: flex; flex-direction: column; }
.account-menu .alerts { margin: 0 0 6px; padding-bottom: 6px; border-bottom: 1px solid var(--si-hair-faint); }
.account-menu .menu-item { display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 12px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-2); font: var(--si-font-body); text-align: left; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out); }
.account-menu .menu-item svg { color: var(--si-ink-3); }
.account-menu .menu-item:hover, .account-menu .menu-item:focus-visible { background: var(--si-hover-bg); color: var(--si-ink-1); outline: none; }
.account-menu .menu-rule { height: 1px; margin: 4px 6px; background: var(--si-hair-faint); }
</style>
