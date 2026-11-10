import { ElMessage } from 'element-plus'
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'
import Assistant from '@/views/Assistant.vue'
import Login from '@/views/Login.vue'
import { SETTING_TABS, type Perm } from '@/utils/permissions'
// 首屏只带登录页、主布局与智能助手首页，其余页面按路由懒加载（V31Q-114）

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 */
    public?: boolean
    /** 仅管理员可访问 */
    admin?: boolean
    /** 持有任一权限点才能进入（V31-U5F-07）；设置分页按 SETTING_TABS 自动填写 */
    perms?: Perm[]
  }
}

export const HOME_PATH = '/assistant'

/** 首页：智能助手；没有该权限时依次退到智能审核、会议纪要，最后是翻译（本地翻译人人可用） */
function homeFor(can: (...p: Perm[]) => boolean): string {
  return can('use.assistant') ? '/assistant' : can('use.review') ? '/review/new' : can('use.meeting') ? '/meeting' : '/translate'
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    {
      path: '/',
      component: MainLayout,
      children: [
        { path: '', redirect: HOME_PATH },
        { path: 'review/new', name: 'review-new', component: () => import('@/views/ReviewNew.vue'), meta: { perms: ['use.review'] } },
        { path: 'review/:taskId', name: 'review-result', component: () => import('@/views/ReviewResult.vue') },
        { path: 'history', name: 'history', component: () => import('@/views/History.vue') },
        { path: 'assistant', name: 'assistant', component: Assistant, meta: { perms: ['use.assistant'] } },
        { path: 'assistant/:sessionId', name: 'assistant-session', component: Assistant },
        // 会议纪要与翻译是侧栏独立菜单（V31-U5），底层沿用助手会话
        { path: 'meeting', name: 'meeting', component: () => import('@/views/Meeting.vue'), meta: { perms: ['use.meeting'] } },
        { path: 'meeting/:sessionId', name: 'meeting-session', component: () => import('@/views/Meeting.vue') },
        { path: 'translate', name: 'translate', component: () => import('@/views/Translate.vue') },
        { path: 'translate/:sessionId', name: 'translate-session', component: () => import('@/views/Translate.vue') },
        {
          path: 'settings',
          component: () => import('@/views/settings/SettingsLayout.vue'),
          children: [
            // 设置对所有人可见，“个人”在最前；管理分页仍按角色校验（V31Q-95）
            { path: '', redirect: '/settings/personal' },
            { path: 'personal', name: 'settings-personal', component: () => import('@/views/settings/Personal.vue') },
            { path: 'users', name: 'settings-users', component: () => import('@/views/settings/Users.vue') },
            { path: 'roles', name: 'settings-roles', component: () => import('@/views/settings/Roles.vue') },
            { path: 'model', name: 'settings-model', component: () => import('@/views/settings/Model.vue') },
            { path: 'requirements', name: 'settings-requirements', component: () => import('@/views/settings/Requirements.vue') },
            { path: 'laws', name: 'settings-laws', component: () => import('@/views/settings/Laws.vue') },
            { path: 'templates', name: 'settings-templates', component: () => import('@/views/settings/Templates.vue') },
            { path: 'knowledge', name: 'settings-knowledge', component: () => import('@/views/settings/Knowledge.vue') },
            { path: 'redaction', name: 'settings-redaction', component: () => import('@/views/settings/Redaction.vue') },
            { path: 'data', name: 'settings-data', component: () => import('@/views/settings/Data.vue') },
          ],
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: HOME_PATH },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // 首次导航时向服务端确认登录状态（刷新页面后恢复登录）
  // 服务器暂不可用时在这里等待并自动重试（App 显示提示），不让首次导航失败成白屏
  if (!auth.loaded) await auth.load()

  if (to.meta.public) {
    // 已登录再访问登录页，回到首页
    return auth.isLoggedIn ? { path: homeFor(auth.can) } : true
  }

  // 未登录：去登录页，登录后回到原来要去的地方
  if (!auth.isLoggedIn) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }

  // 强制改密：只能停留在首页，由主布局弹出不可关闭的改密对话框（不再按权限跳转）
  if (auth.mustChangePassword) {
    return to.path === HOME_PATH ? true : { path: HOME_PATH }
  }

  // 非管理员不能进入管理员页面
  if (to.meta.admin && !auth.isAdmin) {
    ElMessage.error('没有权限访问该页面')
    return { path: homeFor(auth.can) }
  }

  const tab = to.path.startsWith('/settings/') ? SETTING_TABS.find(t => to.path === `/settings/${t.name}`) : undefined
  const perms = tab?.perms ?? to.meta.perms ?? []
  if (!auth.can(...perms)) {
    if (tab) return { path: '/settings/personal' }
    const home = homeFor(auth.can)
    return to.path === home ? true : { path: home }
  }

  return true
})

// 升级后旧页面再去取已不存在的懒加载分块会失败：整页重新加载到目标地址，取到新版本
router.onError((error, to) => {
  const message = String((error as { message?: unknown })?.message ?? error)
  if (/dynamically imported module|Importing a module script failed|error loading dynamically/i.test(message)) {
    window.location.assign(to.fullPath)
  }
})
