import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { authApi, type AdminAlert, type User } from '@/api/auth'
import { ApiError, setCsrfToken } from '@/api/http'
import type { Perm } from '@/utils/permissions'

/** 登录状态。会话本身在服务端（HttpOnly Cookie），这里只保存当前用户和 CSRF 令牌。 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const alerts = ref<AdminAlert[]>([])
  const dismissedAlerts = ref(new Set<string>())
  const alertKey = (alert: AdminAlert) => JSON.stringify([alert.code ?? '', alert.message])
  const visibleAlerts = computed(() => alerts.value.filter(alert => !dismissedAlerts.value.has(alertKey(alert))))
  function dismissAlert(alert: AdminAlert): void { dismissedAlerts.value.add(alertKey(alert)) }
  // 是否已向服务端确认过登录状态（路由守卫只在首次导航时探测一次）
  const loaded = ref(false)

  const isLoggedIn = computed(() => user.value !== null)
  // 权限点（V31-U5F-07）：持有任一即可；空列表表示所有登录用户
  const perms = computed(() => new Set(user.value?.permissions ?? []))
  function can(...codes: Perm[]): boolean { return codes.length === 0 || codes.some(c => perms.value.has(c)) }
  // 有任何管理权限（非“使用”类）即可看到管理员提醒
  const isAdmin = computed(() => [...perms.value].some(p => !p.startsWith('use.')))
  const mustChangePassword = computed(() => user.value?.must_change_password === true)

  function clear(): void {
    user.value = null
    alerts.value = []
    dismissedAlerts.value.clear()
    setCsrfToken('')
  }

  /** 向服务端确认当前登录状态；未登录不算错误。连不上服务器（502、断网等）时抛出，loaded 保持不变。 */
  async function refresh(): Promise<void> {
    try {
      const me = await authApi.me()
      if (user.value?.id !== me.user.id) dismissedAlerts.value.clear()
      user.value = me.user
      alerts.value = me.alerts
      setCsrfToken(me.csrf_token)
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) throw e
      clear()
    }
    loaded.value = true
  }

  // 首次打开时服务器暂不可用（启动、升级、重启中）：显示提示并每 5 秒重试，恢复后继续原来的导航（V31Q-113）
  const unavailable = ref(false)
  let wake: (() => void) | null = null
  let loading: Promise<void> | null = null
  function load(): Promise<void> {
    loading ??= retryUntilLoaded().finally(() => { loading = null })
    return loading
  }
  async function retryUntilLoaded(): Promise<void> {
    for (;;) {
      try {
        await refresh()
        unavailable.value = false
        return
      } catch {
        unavailable.value = true
        await new Promise<void>(resolve => {
          const timer = setTimeout(resolve, 5000)
          wake = () => { clearTimeout(timer); resolve() }
        })
        wake = null
      }
    }
  }
  function retryNow(): void { wake?.() }

  async function login(username: string, password: string): Promise<void> {
    clear()
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
    visibleAlerts,
    dismissAlert,
    loaded,
    unavailable,
    load,
    retryNow,
    isLoggedIn,
    isAdmin,
    can,
    mustChangePassword,
    clear,
    refresh,
    login,
    logout,
    changePassword,
  }
})
