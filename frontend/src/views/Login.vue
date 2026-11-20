<script setup lang="ts">
import type { FormInstance, FormRules, InputInstance } from 'element-plus'
import { EyeOff, ListChecks, Lock, ShieldCheck, User } from '@lucide/vue'
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
// 记住密码（V321Q-50）：密码只交给浏览器自带的密码管理器保存，本系统不把密码写进本地存储。
// 需要 Edge / Chrome，且页面证书受信任（导入根证书后）；不满足时只记住用户名。
type PasswordCredentialCtor = new (data: { id: string; password: string }) => Credential
const PasswordCred = (window as unknown as { PasswordCredential?: PasswordCredentialCtor }).PasswordCredential
// 根证书下载页由 Caddy 在 http 的 /cert/ 提供（SPEC 20.4）
const certUrl = `http://${location.hostname}/cert/`
onMounted(async () => {
  if (remember.value) passwordRef.value?.focus()
  else usernameRef.value?.focus()
  if (!remember.value || !PasswordCred) return
  try {
    const saved = await navigator.credentials.get({ password: true, mediation: 'optional' } as CredentialRequestOptions) as (Credential & { password?: string }) | null
    if (saved?.password && !form.password) { form.username = saved.id; form.password = saved.password }
  } catch {
    // 浏览器不提供已保存的密码时照常手动输入。
  }
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
    if (remember.value && PasswordCred) {
      try { await navigator.credentials.store(new PasswordCred({ id: username, password: form.password })) } catch { /* 浏览器拒绝保存时只记住用户名 */ }
    }
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
        <div class="si-glow" aria-hidden="true"><i /><i /><i /></div>
        <div class="brand-copy">
          <SiLogo size="hero" intro />
          <h2>商务文件的安全初审助手</h2>
          <p class="brand-description">原件留在本机，真实名称遮住后才交给模型，结果回来在本机还原。</p>
          <ul class="points">
            <li><span class="pi"><Lock :size="18" :stroke-width="1.75" /></span>原件不出内网</li>
            <li><span class="pi"><EyeOff :size="18" :stroke-width="1.75" /></span>名称发出前遮住</li>
            <li><span class="pi"><ListChecks :size="18" :stroke-width="1.75" /></span>几分钟出问题清单</li>
          </ul>
        </div>
        <footer>V3.4.0 · 仅限公司内网使用</footer>
      </section>
      <div class="login-pane">
        <SiLogo class="pane-logo" size="greet" />
        <section class="login-form si-card" aria-labelledby="login-title">
          <h1 id="login-title">登录</h1>
          <p class="subtitle">请使用内部分发的账号登录</p>
          <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
            <el-form-item label="用户名" prop="username">
              <el-input ref="usernameRef" v-model="form.username" aria-label="用户名" autocomplete="username" @input="errorText = ''"><template #prefix><User :size="16" :stroke-width="1.75" /></template></el-input>
            </el-form-item>
            <el-form-item label="密码" prop="password" :error="errorText">
              <el-input ref="passwordRef" v-model="form.password" aria-label="密码" type="password" show-password autocomplete="current-password" @input="errorText = ''"><template #prefix><Lock :size="16" :stroke-width="1.75" /></template></el-input>
            </el-form-item>
            <div class="remember-row"><el-checkbox v-model="remember">记住账号</el-checkbox><span class="hint">密码由浏览器保存</span></div>
            <el-button type="primary" native-type="submit" :loading="submitting" class="submit">登录</el-button>
          </el-form>
          <p class="account-help">账号由管理员统一分发，忘记密码请联系管理员重置</p>
          <a class="cert-link" :href="certUrl" target="_blank" rel="noopener"><ShieldCheck :size="16" :stroke-width="1.75" /><span>下载根证书</span><span class="hint">浏览器提示“不安全”时安装</span></a>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
:global(body:has(.login-page)) { min-width: 0; }
.login-page { min-height: 100vh; min-height: 100svh; background: var(--si-bg-canvas); }
.login-shell { display: grid; grid-template-columns: 55fr 45fr; min-height: inherit; }
/* 品牌区：深色底，氛围光只在这里（澄明 8.1） */
.brand-area { min-width: 0; box-sizing: border-box; position: relative; display: flex; flex-direction: column; justify-content: center; padding: 64px clamp(40px, 6vw, 120px) 96px; overflow: hidden; background: var(--si-island-bg); color: var(--si-island-ink); }
.brand-copy, footer { position: relative; }
.brand-copy :deep(.wordmark) { color: var(--si-island-ink); }
.brand-copy :deep(.wordmark em) { color: var(--si-brand-2); }
h2 { font: var(--si-font-display); margin: 40px 0 16px; color: var(--si-island-ink); }
.brand-description { margin: 0; max-width: 520px; font: var(--si-font-reading); font-size: calc(16px * var(--si-fs)); color: color-mix(in srgb, var(--si-island-ink) 70%, transparent); }
.points { display: flex; flex-wrap: wrap; gap: 12px 28px; margin: 40px 0 0; padding: 0; list-style: none; font: var(--si-font-label); color: var(--si-island-ink); }
.points li { display: flex; align-items: center; gap: 10px; }
.pi { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: color-mix(in srgb, var(--si-island-ink) 10%, transparent); color: var(--si-brand-2); }
footer { position: absolute; bottom: 28px; left: clamp(40px, 6vw, 120px); right: 32px; font: var(--si-font-caption); color: color-mix(in srgb, var(--si-island-ink) 55%, transparent); }
.login-pane { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 24px; padding: 40px 24px; box-sizing: border-box; min-width: 0; }
.pane-logo { display: none; }
.login-form { width: 400px; max-width: 100%; padding: 36px 36px 28px; border-radius: var(--si-radius-sheet); box-shadow: var(--si-shadow-2); box-sizing: border-box; }
h1 { font: var(--si-font-title-lg); margin: 0 0 6px; }
.subtitle { color: var(--si-ink-3); margin: 0 0 28px; }
.login-form :deep(.el-input) { --el-input-height: 44px; }
.login-form :deep(.el-form-item) { margin-bottom: 22px; }
.remember-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; column-gap: 8px; margin: -4px 0 20px; }
.submit { width: 100%; height: 44px; }
.account-help { font: var(--si-font-meta); color: var(--si-ink-3); margin: 20px 0 0; }
.cert-link { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--si-hair); color: var(--si-blue-ink); font: var(--si-font-label); text-decoration: none; }
.cert-link:hover > span:first-of-type { text-decoration: underline; }
.cert-link .hint { font: var(--si-font-caption); color: var(--si-ink-3); }
/* 宽度小于 1280：隐去品牌区，只留标志和登录卡 */
@media (max-width: 1279px) {
  .login-shell { grid-template-columns: 1fr; }
  .brand-area { display: none; }
  .pane-logo { display: inline-flex; }
}
</style>
