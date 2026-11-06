import { ElMessage } from 'element-plus'
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'
import History from '@/views/History.vue'
import Assistant from '@/views/Assistant.vue'
import Meeting from '@/views/Meeting.vue'
import Translate from '@/views/Translate.vue'
import Login from '@/views/Login.vue'
import ReviewNew from '@/views/ReviewNew.vue'
import ReviewResult from '@/views/ReviewResult.vue'
import Data from '@/views/settings/Data.vue'
import Model from '@/views/settings/Model.vue'
import Redaction from '@/views/settings/Redaction.vue'
import Requirements from '@/views/settings/Requirements.vue'
import Laws from '@/views/settings/Laws.vue'
import Templates from '@/views/settings/Templates.vue'
import Knowledge from '@/views/settings/Knowledge.vue'
import SettingsLayout from '@/views/settings/SettingsLayout.vue'
import Users from '@/views/settings/Users.vue'
import Personal from '@/views/settings/Personal.vue'
import Roles from '@/views/settings/Roles.vue'
import { SETTING_TABS, type Perm } from '@/utils/permissions'

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
        { path: 'review/new', name: 'review-new', component: ReviewNew, meta: { perms: ['use.review'] } },
        { path: 'review/:taskId', name: 'review-result', component: ReviewResult },
        { path: 'history', name: 'history', component: History },
        { path: 'assistant', name: 'assistant', component: Assistant, meta: { perms: ['use.assistant'] } },
        { path: 'assistant/:sessionId', name: 'assistant-session', component: Assistant },
        // 会议纪要与翻译是侧栏独立菜单（V31-U5），底层沿用助手会话
        { path: 'meeting', name: 'meeting', component: Meeting, meta: { perms: ['use.meeting'] } },
        { path: 'meeting/:sessionId', name: 'meeting-session', component: Meeting },
        { path: 'translate', name: 'translate', component: Translate },
        { path: 'translate/:sessionId', name: 'translate-session', component: Translate },
        {
          path: 'settings',
          component: SettingsLayout,
          children: [
            // 设置对所有人可见，“个人”在最前；管理分页仍按角色校验（V31Q-95）
            { path: '', redirect: '/settings/personal' },
            { path: 'personal', name: 'settings-personal', component: Personal },
            { path: 'users', name: 'settings-users', component: Users },
            { path: 'roles', name: 'settings-roles', component: Roles },
            { path: 'model', name: 'settings-model', component: Model },
            { path: 'requirements', name: 'settings-requirements', component: Requirements },
            { path: 'laws', name: 'settings-laws', component: Laws },
            { path: 'templates', name: 'settings-templates', component: Templates },
            { path: 'knowledge', name: 'settings-knowledge', component: Knowledge },
            { path: 'redaction', name: 'settings-redaction', component: Redaction },
            { path: 'data', name: 'settings-data', component: Data },
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
  if (!auth.loaded) await auth.refresh()

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
