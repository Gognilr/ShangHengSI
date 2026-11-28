<script setup lang="ts">
import type { FormInstance, FormRules, InputInstance } from 'element-plus'
import { Check, EyeOff, HardDrive, Lock, ShieldCheck, Sparkle, Undo2, User } from '@lucide/vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import SiLogo from '@/components/brand/SiLogo.vue'
import { useAuthStore } from '@/stores/auth'
import { useFx } from '@/utils/motion'
import { toast } from '@/utils/toast'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const fx = useFx()
const usernameRef = ref<InputInstance>()
const passwordRef = ref<InputInstance>()
const newRef = ref<InputInstance>()
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

/**
 * 首次改密并进登录卡（澄明二版 6.3，V36Q-11）：首次登录或被管理员重置密码后，登录成功的同一张卡切到第二步，
 * 不进入主框架。已登录但须改密的会话（例如在第二步刷新）由路由守卫送回这里。
 */
const step = computed<1 | 2>(() => auth.isLoggedIn && auth.mustChangePassword ? 2 : 1)

onMounted(async () => {
  if (step.value === 2) { void nextTick(() => newRef.value?.focus()); return }
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
  return typeof r === 'string' && r.startsWith('/') && !r.startsWith('//') && !r.startsWith('/login') ? r : '/'
}

async function submit() {
  errorText.value = ''
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    const username = form.username.trim()
    await auth.login(username, form.password)
    // 须改密时不把初始密码交给浏览器保存：第二步保存的才是要记住的密码
    if (remember.value && PasswordCred && !auth.mustChangePassword) {
      try { await navigator.credentials.store(new PasswordCred({ id: username, password: form.password })) } catch { /* 浏览器拒绝保存时只记住用户名 */ }
    }
    // 仅在登录成功后保存或删除用户名，失败时不改变已记住的值。
    try {
      if (remember.value) localStorage.setItem(USERNAME_KEY, username)
      else localStorage.removeItem(USERNAME_KEY)
    } catch {
      // 存储失败不影响会话，也不保存密码或其他认证数据。
    }
    if (!auth.mustChangePassword) await router.replace(safeRedirect())
  } catch (e) {
    errorText.value = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
  } finally {
    form.password = ''
    submitting.value = false
  }
}

/* ---- 第二步：设置新密码 ---- */
const pwdRef = ref<FormInstance>()
const pwd = reactive({ next: '', again: '' })
const pwdErr = reactive({ next: '', again: '' })
const saving = ref(false)
// 规则以后端为准（SPEC 6.6）：至少 8 位，同时含英文字母和数字，不超过 128 位；这里先查一遍，免得白跑一趟
function checkNext(_r: unknown, v: string, cb: (e?: Error) => void) {
  if (v.length < 8) return cb(new Error('至少 8 位'))
  if (v.length > 128) return cb(new Error('不能超过 128 位'))
  if (!/[A-Za-z]/.test(v) || !/\d/.test(v)) return cb(new Error('须同时包含字母和数字'))
  cb()
}
function checkAgain(_r: unknown, v: string, cb: (e?: Error) => void) { cb(v === pwd.next ? undefined : new Error('两次输入的密码不一致')) }
const pwdRules: FormRules = {
  next: [{ required: true, message: '请输入新密码', trigger: 'blur' }, { validator: checkNext, trigger: 'blur' }],
  again: [{ required: true, message: '请再次输入新密码', trigger: 'blur' }, { validator: checkAgain, trigger: 'blur' }],
}
watch(step, s => {
  pwd.next = ''; pwd.again = ''; pwdErr.next = ''; pwdErr.again = ''
  if (s === 2) void nextTick(() => newRef.value?.focus())
  else void nextTick(() => (form.username ? passwordRef : usernameRef).value?.focus())
})
async function savePassword() {
  pwdErr.next = ''; pwdErr.again = ''
  if (!pwdRef.value || !(await pwdRef.value.validate().catch(() => false))) return
  saving.value = true
  const username = auth.user?.username ?? ''
  try {
    await auth.changePassword('', pwd.next)
    // 浏览器密码管理器：表单里的隐藏用户名 + new-password 两个框已能让它提示更新；支持凭据接口的再明确交一次
    if (PasswordCred && username) {
      try { await navigator.credentials.store(new PasswordCred({ id: username, password: pwd.next })) } catch { /* 浏览器不保存时照常进入 */ }
    }
    pwd.next = ''; pwd.again = ''
    toast('密码已更新，其他设备上的登录已退出')
    await router.replace(safeRedirect())
  } catch (e) {
    // 后端的错误落在对应字段下方：新旧相同、强度不够写在新密码下，其余（网络等）也写在新密码下
    pwdErr.next = e instanceof ApiError ? e.message : '保存失败，请稍后重试'
  } finally {
    saving.value = false
  }
}
async function quit() {
  await auth.logout()
}

/* ---- 品牌区开场往返线：纸 → 遮住 → 星点 → 还原，约 2.6 秒走完定格 ---- */
const STOPS = [
  { x: 70, zone: 'l', icon: HardDrive, title: '原件不出内网', sub: '在本机读取' },
  { x: 210, zone: 'l', icon: EyeOff, title: '名称发出前遮住', sub: '换成代号再发送' },
  { x: 350, zone: 'm', icon: Sparkle, title: '模型只见代号', sub: '经网关复检后发出' },
  { x: 490, zone: 'l', icon: Undo2, title: '回到本机还原', sub: '换回真实名称' },
] as const
const tripAria = STOPS.map((s, i) => `第 ${i + 1} 站 ${s.title}`).join('，')
</script>

<template>
  <div class="login-page">
    <div class="login-shell">
      <section class="brand-area" aria-label="商衡SI">
        <div class="si-glow" aria-hidden="true"><i /><i /><i /></div>
        <div class="brand-copy">
          <SiLogo size="hero" intro class="brand-mark" />
          <h2>商务文件的安全初审助手</h2>
          <p class="brand-description">原件留在本机，真实名称遮住后才交给模型，结果回来在本机还原。</p>
          <!-- 衡线标志的横线伸出纸边，延长成这条往返线：标志开场后 0.6 秒开始走 -->
          <div class="lb-trip" role="img" :aria-label="`一次往返：${tripAria}`">
            <svg viewBox="0 0 560 64" aria-hidden="true">
              <defs>
                <linearGradient id="lt-grad" x1="70" x2="490" gradientUnits="userSpaceOnUse">
                  <stop offset="0" class="g-l" /><stop offset=".5" class="g-l" /><stop offset=".55" class="g-m" />
                  <stop offset=".78" class="g-m" /><stop offset=".9" class="g-l" />
                </linearGradient>
              </defs>
              <path class="lt-base" d="M10 32H550" />
              <path class="lt-run" d="M70 32H490" stroke="url(#lt-grad)" />
              <g v-for="(s, i) in STOPS" :key="i" class="lt-stop" :class="`z-${s.zone}`" :style="{ '--i': i }">
                <circle :cx="s.x" cy="32" r="13" class="lt-n" />
                <component :is="s.icon" :x="s.x - 7" y="25" :size="14" :stroke-width="2.25" class="lt-ic" />
              </g>
            </svg>
            <div class="lt-labels">
              <div v-for="(s, i) in STOPS" :key="i" :style="{ '--i': i }"><b>{{ s.title }}</b><span>{{ s.sub }}</span></div>
            </div>
          </div>
        </div>
        <footer><span>商衡SI V3.6.1</span><span>仅限公司内网使用</span></footer>
      </section>
      <div class="login-pane">
        <SiLogo class="pane-logo" size="greet" />
        <section class="login-form si-card" :aria-labelledby="step === 1 ? 'login-title' : 'pwd-title'">
          <Transition :name="fx === 'full' ? 'card-slide' : fx === 'lite' ? 'card-fade' : ''" mode="out-in">
            <div v-if="step === 1" key="s1" class="step">
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
            </div>
            <div v-else key="s2" class="step">
              <ol class="steps" aria-label="步骤">
                <li class="done"><span class="sd"><Check :size="11" :stroke-width="3" /></span>登录</li>
                <li class="now" aria-current="step"><span class="sd">2</span>设置新密码</li>
              </ol>
              <h1 id="pwd-title">设置新密码</h1>
              <p class="subtitle">这是初始密码，请换成只有你知道的密码</p>
              <el-form ref="pwdRef" :model="pwd" :rules="pwdRules" label-position="top" @submit.prevent="savePassword">
                <!-- 隐藏的用户名：让浏览器的密码管理器知道这是哪个账号的新密码 -->
                <input class="sr-only" type="text" name="username" autocomplete="username" :value="auth.user?.username ?? ''" readonly tabindex="-1" aria-hidden="true">
                <el-form-item label="新密码" prop="next" :error="pwdErr.next">
                  <el-input ref="newRef" v-model="pwd.next" name="new-password" aria-label="新密码" type="password" show-password autocomplete="new-password" maxlength="128" @input="pwdErr.next = ''"><template #prefix><Lock :size="16" :stroke-width="1.75" /></template></el-input>
                  <p class="rule">至少 8 位，同时包含字母和数字，不能与初始密码相同</p>
                </el-form-item>
                <el-form-item label="再次输入" prop="again" :error="pwdErr.again">
                  <el-input v-model="pwd.again" name="confirm-password" aria-label="再次输入新密码" type="password" show-password autocomplete="new-password" maxlength="128" @input="pwdErr.again = ''"><template #prefix><Lock :size="16" :stroke-width="1.75" /></template></el-input>
                </el-form-item>
                <el-button type="primary" native-type="submit" :loading="saving" class="submit">保存并进入</el-button>
              </el-form>
              <p class="quit">不是你的账号？<el-button link type="primary" :disabled="saving" @click="quit">退出登录</el-button></p>
            </div>
          </Transition>
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
/* 标志 96 加字标“商衡SI”：深色品牌区里字标用浅色，SI 用衡青 */
.brand-copy .brand-mark { flex-direction: row; align-items: center; gap: 20px; margin-bottom: 4px; }
.brand-mark :deep(.wordmark) { color: var(--si-island-ink); }
.brand-mark :deep(.wordmark em) { color: var(--si-brand-2); }
h2 { font: var(--si-font-display); margin: 32px 0 16px; color: var(--si-island-ink); }
.brand-description { margin: 0; max-width: 560px; font: var(--si-font-reading); font-size: calc(16px * var(--si-fs)); color: color-mix(in srgb, var(--si-island-ink) 70%, transparent); }
footer { position: absolute; bottom: 28px; left: clamp(40px, 6vw, 120px); right: 32px; display: flex; gap: 16px; font: var(--si-font-caption); color: color-mix(in srgb, var(--si-island-ink) 45%, transparent); }

/* 开场往返线：前半青，过“遮住”后变紫，到“还原”前变回青；四个站点落在四列说明的中心 */
.lb-trip { width: 560px; max-width: 100%; margin-top: 48px; }
.lb-trip svg { display: block; width: 100%; height: auto; overflow: visible; }
.g-l { stop-color: var(--si-brand-2); }
.g-m { stop-color: var(--si-ai-2); }
.lt-base { stroke: color-mix(in srgb, var(--si-island-ink) 14%, transparent); stroke-width: 2; }
.lt-run { fill: none; stroke-width: 3; stroke-linecap: round; stroke-dasharray: 420; }
.lt-stop { --zc: var(--si-brand-2); }
.lt-stop.z-m { --zc: var(--si-ai-2); }
.lt-n { fill: var(--si-island-bg); stroke: var(--zc); stroke-width: 2; }
.lt-ic { color: var(--zc); }
.lt-labels { display: grid; grid-template-columns: repeat(4, 1fr); margin-top: 8px; text-align: center; }
.lt-labels b { display: block; font: var(--si-font-label); font-weight: 600; color: var(--si-island-ink); }
.lt-labels span { display: block; margin-top: 2px; font: var(--si-font-meta); color: color-mix(in srgb, var(--si-island-ink) 58%, transparent); }
html[data-fx="full"] .lt-run { animation: lt-draw 2000ms cubic-bezier(.65, 0, .35, 1) 600ms both; }
html[data-fx="full"] .lt-stop { animation: lt-pop var(--si-d3) var(--si-ease-spring) calc(600ms + var(--i) * 660ms) both; transform-box: fill-box; transform-origin: center; }
html[data-fx="full"] .lt-labels > div { animation: lt-rise 400ms var(--si-ease-out) calc(600ms + var(--i) * 660ms) both; }
@keyframes lt-draw { from { stroke-dashoffset: 420; } to { stroke-dashoffset: 0; } }
@keyframes lt-pop { from { opacity: 0; transform: scale(.4); } }
@keyframes lt-rise { from { opacity: 0; transform: translateY(6px); } }

.login-pane { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 24px; padding: 40px 24px; box-sizing: border-box; min-width: 0; }
.pane-logo { display: none; }
.login-form { width: 400px; max-width: 100%; padding: 36px 36px 28px; overflow: hidden; border-radius: var(--si-radius-sheet); box-shadow: var(--si-shadow-2); box-sizing: border-box; }
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

/* 第二步 */
.steps { display: flex; align-items: center; gap: 0; margin: 0 0 20px; padding: 0; list-style: none; font: var(--si-font-label-sm); color: var(--si-ink-3); }
.steps li { display: flex; align-items: center; gap: 8px; }
.steps li + li::before { content: ""; width: 28px; height: 2px; margin: 0 10px; border-radius: 2px; background: var(--si-trip-l); }
.sd { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; font: var(--si-font-badge); font-weight: 600; }
.steps .done { color: var(--si-ink-2); }
.steps .done .sd { background: var(--si-trip-l); color: var(--si-white); }
.steps .now { color: var(--si-ink-1); font-weight: 600; }
.steps .now .sd { border: 2px solid var(--si-trip-l); color: var(--si-blue-ink); box-sizing: border-box; }
.rule { width: 100%; margin: 6px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
/* 字段错误出现时，规则说明让位给错误提示 */
.login-form :deep(.is-error) .rule { display: none; }
.quit { display: flex; align-items: center; justify-content: center; gap: 2px; margin: 18px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* 卡片内容切换：完整档横向平移 320ms，精简档淡入，关闭档直接切换 */
.card-slide-enter-active, .card-slide-leave-active { transition: transform 160ms var(--si-ease-out), opacity 160ms var(--si-ease-out); }
.card-slide-enter-from { transform: translateX(48px); opacity: 0; }
.card-slide-leave-to { transform: translateX(-48px); opacity: 0; }
.card-fade-enter-active, .card-fade-leave-active { transition: opacity 160ms var(--si-ease-out); }
.card-fade-enter-from, .card-fade-leave-to { opacity: 0; }

/* 宽度小于 1280：隐去品牌区，只留标志和登录卡 */
@media (max-width: 1279px) {
  .login-shell { grid-template-columns: 1fr; }
  .brand-area { display: none; }
  .pane-logo { display: inline-flex; }
}
</style>
