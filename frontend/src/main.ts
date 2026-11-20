import ElementPlus, { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import '@/styles/fonts.css'
import '@/styles/theme.css'
import '@/styles/skin.css'
import '@/styles/common.css'
import '@/styles/motion.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { setHttpHooks } from '@/api/http'
import App from './App.vue'
import { HOME_PATH, router } from './router'
import { useAppearanceStore } from './stores/appearance'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
useAppearanceStore(pinia).init()

// 出错提示要读原因和办法，1.8 秒太短：未指定时长的 ElMessage.error 改为 4 秒（澄明 8.10 的 1.8 秒只用于普通提示）
const showError = ElMessage.error
ElMessage.error = ((options, appContext) => showError(
  typeof options === 'string' ? { message: options, duration: 4000 }
    : options && typeof options === 'object' && 'message' in options ? { duration: 4000, ...options } : options,
  appContext)) as typeof ElMessage.error

const auth = useAuthStore(pinia)
setHttpHooks({
  // 会话失效：清除登录状态，回到登录页（带上当前地址，登录后返回）
  onUnauthorized: () => {
    auth.clear()
    const current = router.currentRoute.value
    if (current.name !== 'login') {
      void router.replace({
        name: 'login',
        query: current.fullPath === '/' ? {} : { redirect: current.fullPath },
      })
    }
  },
  // 服务端要求先改密：重新读取登录状态，主布局会弹出改密对话框
  onPasswordChangeRequired: () => {
    void auth.refresh().then(() => router.replace({ path: HOME_PATH }))
  },
})

app.mount('#app')
