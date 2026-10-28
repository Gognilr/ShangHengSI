import { ElMessage } from 'element-plus'
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'
import History from '@/views/History.vue'
import Assistant from '@/views/Assistant.vue'
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

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 */
    public?: boolean
    /** 仅管理员可访问 */
    admin?: boolean
    adminScope?: 'sys' | 'biz'
  }
}

export const HOME_PATH = '/review/new'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    {
      path: '/',
      component: MainLayout,
      children: [
        { path: '', redirect: HOME_PATH },
        { path: 'review/new', name: 'review-new', component: ReviewNew },
        { path: 'review/:taskId', name: 'review-result', component: ReviewResult },
        { path: 'history', name: 'history', component: History },
        { path: 'assistant', name: 'assistant', component: Assistant },
        { path: 'assistant/:sessionId', name: 'assistant-session', component: Assistant },
        {
          path: 'settings',
          component: SettingsLayout,
          meta: { admin: true },
          children: [
            { path: '', redirect: '/settings/users' },
            { path: 'users', name: 'settings-users', component: Users, meta: { adminScope: 'sys' } },
            { path: 'model', name: 'settings-model', component: Model, meta: { adminScope: 'sys' } },
            { path: 'requirements', name: 'settings-requirements', component: Requirements, meta: { adminScope: 'biz' } },
            { path: 'laws', name: 'settings-laws', component: Laws, meta: { adminScope: 'biz' } },
            { path: 'templates', name: 'settings-templates', component: Templates, meta: { adminScope: 'biz' } },
            { path: 'knowledge', name: 'settings-knowledge', component: Knowledge, meta: { adminScope: 'biz' } },
            { path: 'redaction', name: 'settings-redaction', component: Redaction, meta: { adminScope: 'biz' } },
            { path: 'data', name: 'settings-data', component: Data, meta: { adminScope: 'sys' } },
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
    return auth.isLoggedIn ? { path: HOME_PATH } : true
  }

  // 未登录：去登录页，登录后回到原来要去的地方
  if (!auth.isLoggedIn) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }

  // 强制改密：只能停留在首页，由主布局弹出不可关闭的改密对话框
  if (auth.mustChangePassword && to.path !== HOME_PATH) {
    return { path: HOME_PATH }
  }

  // 非管理员不能进入管理员页面
  if (to.meta.admin && !auth.isAdmin) {
    ElMessage.error('没有权限访问该页面')
    return { path: HOME_PATH }
  }

  if ((to.meta.adminScope === 'sys' && !auth.canSysAdmin) || (to.meta.adminScope === 'biz' && !auth.canBizAdmin)) {
    return { path: auth.canSysAdmin ? '/settings/users' : '/settings/requirements' }
  }

  return true
})
