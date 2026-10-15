import { ElMessage } from 'element-plus'
import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { useAuthStore } from '@/stores/auth'
import Login from '@/views/Login.vue'

// M1-08 会用正式的占位页面（ReviewNew.vue 等）替换这里的临时组件
function placeholder(title: string) {
  return defineComponent({
    name: 'PlaceholderView',
    render: () => h('div', { class: 'placeholder' }, `${title}：建设中`),
  })
}

declare module 'vue-router' {
  interface RouteMeta {
    /** 无需登录即可访问 */
    public?: boolean
    /** 仅管理员可访问 */
    admin?: boolean
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
        { path: 'review/new', name: 'review-new', component: placeholder('智能审核') },
        { path: 'history', name: 'history', component: placeholder('审核历史') },
        { path: 'settings', name: 'settings', component: placeholder('设置'), meta: { admin: true } },
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

  return true
})
