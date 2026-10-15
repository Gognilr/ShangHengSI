import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi, type AdminAlert, type User } from '@/api/auth'
import { ApiError, setCsrfToken } from '@/api/http'

/** 登录状态。会话本身在服务端（HttpOnly Cookie），这里只保存当前用户和 CSRF 令牌。 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const alerts = ref<AdminAlert[]>([])
  // 是否已向服务端确认过登录状态（路由守卫只在首次导航时探测一次）
  const loaded = ref(false)

  const isLoggedIn = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const mustChangePassword = computed(() => user.value?.must_change_password === true)

  function clear(): void {
    user.value = null
    alerts.value = []
    setCsrfToken('')
  }

  /** 向服务端确认当前登录状态；未登录不算错误。 */
  async function refresh(): Promise<void> {
    try {
      const me = await authApi.me()
      user.value = me.user
      alerts.value = me.alerts
      setCsrfToken(me.csrf_token)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clear()
      } else {
        throw e
      }
    } finally {
      loaded.value = true
    }
  }

  async function login(username: string, password: string): Promise<void> {
    const res = await authApi.login(username, password)
    user.value = res.user
    setCsrfToken(res.csrf_token)
    loaded.value = true
    // 管理员提醒只在 /auth/me 里返回
    await refresh()
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      clear()
    }
  }

  async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    await authApi.changePassword(oldPassword, newPassword)
    // 改密成功后服务端清除“须改密”标记，重新读取
    await refresh()
  }

  return {
    user,
    alerts,
    loaded,
    isLoggedIn,
    isAdmin,
    mustChangePassword,
    clear,
    refresh,
    login,
    logout,
    changePassword,
  }
})
