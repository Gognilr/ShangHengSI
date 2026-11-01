import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import '@/styles/fonts.css'
import '@/styles/theme.css'
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
