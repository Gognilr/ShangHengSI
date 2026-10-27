<script setup lang="ts">
import type { FormInstance, FormRules, InputInstance } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import SiLogo from '@/components/brand/SiLogo.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const usernameRef = ref<InputInstance>()
const passwordRef = ref<InputInstance>()
const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const remember = ref(false)
const submitting = ref(false)
const errorText = ref('')
const USERNAME_KEY = 'si.login.username'

try {
  const username = localStorage.getItem(USERNAME_KEY)
  if (username) {
    form.username = username
    remember.value = true
  }
} catch {
  // 本地存储不可用时按未记住处理，不影响登录。
}
onMounted(() => {
  if (remember.value) passwordRef.value?.focus()
  else usernameRef.value?.focus()
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

// 只接受站内路径，避免被构造的 redirect 参数跳到别的网站。
function safeRedirect(): string {
  const r = route.query.redirect
  return typeof r === 'string' && r.startsWith('/') && !r.startsWith('//') ? r : '/'
}

async function submit() {
  errorText.value = ''
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    const username = form.username.trim()
    await auth.login(username, form.password)
    // 仅在登录成功后保存或删除用户名，失败时不改变已记住的值。
    try {
      if (remember.value) localStorage.setItem(USERNAME_KEY, username)
      else localStorage.removeItem(USERNAME_KEY)
    } catch {
      // 存储失败不影响会话，也不保存密码或其他认证数据。
    }
    await router.replace(safeRedirect())
  } catch (e) {
    errorText.value = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
  } finally {
    form.password = ''
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-shell">
      <section class="brand-area" aria-label="商衡SI">
        <SiLogo size="hero" show-tagline />
        <div class="brand-rule" />
        <h2>让敏感信息受到保护<br />让商业决策更有依据</h2>
        <p class="brand-description">面向企业商务、合同与法律文件，提供本地脱敏与 AI 智能初审。</p>
        <div class="brand-tags"><span>安全脱敏</span><span>智能审查</span><span>局域网部署</span></div>
      </section>
      <div class="divider" aria-hidden="true" />
      <section class="login-form" aria-labelledby="login-title">
        <h1 id="login-title">欢迎登录</h1>
        <p class="subtitle">请使用内部分发的账号登录</p>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input ref="usernameRef" v-model="form.username" :prefix-icon="User" aria-label="用户名" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input ref="passwordRef" v-model="form.password" :prefix-icon="Lock" aria-label="密码" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <div class="remember-row"><el-checkbox v-model="remember">记住我</el-checkbox><span class="hint">仅记住用户名</span></div>
          <el-alert v-if="errorText" type="error" :closable="false" show-icon :title="errorText" class="error" />
          <el-button type="primary" native-type="submit" :loading="submitting" class="submit">登录</el-button>
        </el-form>
        <p class="account-help">账号由管理员统一分发，忘记密码请联系管理员重置</p>
      </section>
    </div>
    <footer>© 2026 商衡SI · 企业安全脱敏与智能审查平台 · V1.0</footer>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  padding: 64px 0 96px;
  background: var(--el-bg-color-page);
}
.login-shell { width: calc(100% - 128px); max-width: 1200px; display: grid; grid-template-columns: minmax(0, 1fr) 1px 408px; gap: 64px; align-items: center; }
.brand-area { min-width: 0; }
.brand-rule { width: 32px; height: 3px; margin: 32px 0; background: var(--si-brand-gradient); }
h2 { font: var(--si-font-display); margin: 0 0 20px; }
.brand-description { max-width: 440px; font-size: 15px; color: var(--el-text-color-secondary); margin: 0; }
.brand-tags { display: flex; gap: 12px; margin-top: 32px; }
.brand-tags span { padding: 4px 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; color: var(--el-text-color-secondary); background: var(--el-bg-color); font-size: 13px; }
.divider { align-self: stretch; background: var(--el-border-color-light); }
h1 { font: var(--si-font-title-lg); margin: 0 0 4px; }
.subtitle { color: var(--el-text-color-secondary); margin: 0 0 32px; }
.login-form :deep(.el-input) { --el-input-height: 52px; }
.login-form :deep(.el-input__wrapper:focus-within) { outline: 2px solid var(--si-focus-ring); outline-offset: 2px; }
.login-form :deep(.el-form-item) { margin-bottom: 24px; }
.remember-row { display: flex; align-items: center; justify-content: space-between; margin: -4px 0 20px; }
.error { margin-bottom: 16px; }
.submit { width: 100%; height: 52px; }
.account-help { font-size: 13px; color: var(--el-text-color-secondary); margin: 20px 0 0; line-height: 1.8; }
footer { position: absolute; bottom: 24px; left: 0; right: 0; text-align: center; font-size: 12px; color: var(--el-text-color-secondary); }
@media (max-width: 1099px) {
  .brand-area, .divider { display: none; }
  .login-shell { display: block; width: 408px; }
}
</style>
