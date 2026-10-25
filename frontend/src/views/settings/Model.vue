<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { adminModelApi, type ModelSettings, type ModelTestResult, type UsageOut } from '@/api/admin'
import { ApiError } from '@/api/http'
import PageHeader from '@/components/PageHeader.vue'

const formRef = ref<FormInstance>()
const loading = ref(false)
const error = ref('')
const saving = ref(false)
const testing = ref(false)
const testResult = ref<ModelTestResult | null>(null)
const usage = ref<UsageOut | null>(null)
const saved = ref<ModelSettings | null>(null)

const form = reactive({
  base_url: '',
  name: '',
  api_key: '',
  temperature: 0.1,
  max_tokens: 32000,
  timeout_s: 180,
  json_mode: true,
  extra_text: '{}',
  chunk_chars: 60000,
  price_in_per_m: '0.00',
  price_out_per_m: '0.00',
  monthly_call_cap: 0,
  user_daily_cap: 0,
})

function fill(s: ModelSettings) {
  saved.value = s
  Object.assign(form, {
    base_url: s.base_url,
    name: s.name,
    api_key: '',
    temperature: s.temperature,
    max_tokens: s.max_tokens,
    timeout_s: s.timeout_s,
    json_mode: s.json_mode,
    extra_text: JSON.stringify(s.extra_params),
    chunk_chars: s.chunk_chars,
    price_in_per_m: s.price_in_per_m,
    price_out_per_m: s.price_out_per_m,
    monthly_call_cap: s.monthly_call_cap,
    user_daily_cap: s.user_daily_cap,
  })
  savedForm.value = JSON.stringify(form)
}

// 测试连接用的是已保存的设置；表单有未保存的修改时先提示保存（M8-05）
const savedForm = ref('')
const dirty = computed(() => savedForm.value !== '' && JSON.stringify(form) !== savedForm.value)

// 附加请求参数的推荐值（与运维说明第 4 章一致），点按钮填入，不必手写 JSON
const RECOMMENDED_EXTRA = '{"thinking": {"type": "enabled"}, "reasoning_effort": "high"}'
function fillRecommendedExtra() {
  form.extra_text = RECOMMENDED_EXTRA
  formRef.value?.validateField('extra_text').catch(() => undefined)
}

async function loadUsage() {
  try {
    usage.value = await adminModelApi.usage()
  } catch {
    usage.value = null
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    fill(await adminModelApi.get())
  } catch (e) {
    error.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面'
  } finally {
    loading.value = false
  }
  await loadUsage()
}
onMounted(load)

function parseExtra(): Record<string, unknown> | null {
  try {
    const v = JSON.parse(form.extra_text.trim() || '{}')
    if (v && typeof v === 'object' && !Array.isArray(v) && !('messages' in v)) return v as Record<string, unknown>
  } catch {
    // 不是合法 JSON，按无效处理
  }
  return null
}

function validateExtra(_rule: unknown, _value: string, callback: (e?: Error) => void) {
  callback(parseExtra() ? undefined : new Error('附加请求参数须为 JSON 对象，且不能包含 messages'))
}

function validatePrice(_rule: unknown, value: string, callback: (e?: Error) => void) {
  callback(/^\d{1,9}(\.\d{1,4})?$/.test(String(value).trim()) ? undefined : new Error('须为不小于 0、最多 4 位小数的数字'))
}

function validateKey(_rule: unknown, value: string, callback: (e?: Error) => void) {
  const v = value.trim()
  if (v === '' || /^[\x21-\x7e]{8,500}$/.test(v)) return callback()
  callback(new Error('API 密钥须为 8–500 个可见的英文字符，不能含空格'))
}

const rules: FormRules = {
  base_url: [
    { required: true, message: '请输入接口地址', trigger: 'blur' },
    { pattern: /^https?:\/\/\S+$/, message: '须以 http:// 或 https:// 开头，不能含空格', trigger: 'blur' },
    { max: 500, message: '不能超过 500 个字符', trigger: 'blur' },
  ],
  name: [
    { required: true, whitespace: true, message: '请输入模型名称', trigger: 'blur' },
    { max: 100, message: '不能超过 100 个字符', trigger: 'blur' },
  ],
  api_key: [{ validator: validateKey, trigger: 'blur' }],
  extra_text: [{ validator: validateExtra, trigger: 'blur' }],
  price_in_per_m: [{ validator: validatePrice, trigger: 'blur' }],
  price_out_per_m: [{ validator: validatePrice, trigger: 'blur' }],
}

async function save() {
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  const extra = parseExtra()
  if (!extra) return
  saving.value = true
  try {
    const key = form.api_key.trim()
    fill(
      await adminModelApi.save({
        base_url: form.base_url.trim(),
        name: form.name.trim(),
        temperature: form.temperature,
        max_tokens: form.max_tokens,
        timeout_s: form.timeout_s,
        json_mode: form.json_mode,
        extra_params: extra,
        chunk_chars: form.chunk_chars,
        price_in_per_m: form.price_in_per_m.trim(),
        price_out_per_m: form.price_out_per_m.trim(),
        monthly_call_cap: form.monthly_call_cap,
        user_daily_cap: form.user_daily_cap,
        ...(key ? { api_key: key } : {}),
      }),
    )
    testResult.value = null
    ElMessage.success('已保存')
    await loadUsage()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  if (dirty.value) {
    ElMessage.warning('有未保存的修改，请先保存再测试连接')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await adminModelApi.test()
    await loadUsage()
  } catch (e) {
    testResult.value = { ok: false, message: e instanceof ApiError ? e.message : '测试失败，请稍后重试' }
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="settings-page">
    <PageHeader title="模型接口" description="配置云端模型的接口、调用参数与费用上限" />
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" class="gap" />
    <section class="panel usage-panel">
      <h3>本月用量{{ usage ? `（${usage.month}）` : '' }}</h3>
      <div v-if="usage" class="usage-stats">
        <div class="stat"><div class="stat-label">调用次数</div><div class="stat-value">{{ usage.calls }}<small>{{ usage.cap > 0 ? ` / ${usage.cap}` : '（不限）' }}</small></div></div>
        <div class="stat"><div class="stat-label">输入 token</div><div class="stat-value">{{ usage.tokens_in }}</div></div>
        <div class="stat"><div class="stat-label">输出 token</div><div class="stat-value">{{ usage.tokens_out }}</div></div>
        <div class="stat"><div class="stat-label">估算费用（元）</div><div class="stat-value">{{ usage.cost }}</div></div>
      </div>
      <el-empty v-else description="暂时无法读取用量" :image-size="60" />
    </section>
    <el-form ref="formRef" v-loading="loading" :model="form" :rules="rules" label-position="top" class="model-form" @submit.prevent="save">
      <section class="panel">
        <h3>模型接口</h3>
        <div class="model-grid">
          <el-form-item label="接口地址" prop="base_url" class="span-all"><el-input v-model="form.base_url" placeholder="例如 https://api.deepseek.com/v1" /><div class="hint note">更换地址后，需 IT 同步修改虚拟机防火墙白名单。</div></el-form-item>
          <el-form-item label="模型名称" prop="name"><el-input v-model="form.name" /></el-form-item>
          <el-form-item label="API 密钥" prop="api_key"><el-input v-model="form.api_key" type="password" show-password autocomplete="new-password" :placeholder="saved?.api_key_set ? `已设置（末 4 位 ${saved.api_key_last4}），留空表示不修改` : '尚未设置'" /></el-form-item>
          <el-form-item label="附加请求参数" prop="extra_text" class="span-all"><el-input v-model="form.extra_text" type="textarea" :rows="3" /><div class="hint note extra-note"><span>一般不用手动编辑：使用 DeepSeek 时点“填入推荐值”（开启思考模式，思考强度 high），再保存。</span><el-button link type="primary" @click="fillRecommendedExtra">填入推荐值</el-button></div></el-form-item>
        </div>
        <el-alert v-if="testResult && !dirty":type="testResult.ok ? 'success' : 'error'" :closable="false" show-icon :title="testResult.ok ? `连接成功：模型 ${testResult.model}，耗时 ${testResult.duration_ms} 毫秒` : `连接失败：${testResult.message}`" />
        <div class="actions"><span v-if="dirty" class="hint unsaved">有未保存的修改，请先点页面底部“保存”再测试</span><span v-else class="hint">测试的是已保存的设置</span><el-button :loading="testing" @click="testConnection">测试连接</el-button></div>
      </section>
      <section class="panel">
        <h3>调用参数</h3>
        <div class="model-grid three-cols">
          <el-form-item label="温度（0–2）"><el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" :precision="2" /></el-form-item>
          <el-form-item label="最大输出 token"><el-input-number v-model="form.max_tokens" :min="1" :max="1000000" :step="1000" /></el-form-item>
          <el-form-item label="超时（秒）"><el-input-number v-model="form.timeout_s" :min="10" :max="600" /></el-form-item>
          <el-form-item label="JSON 输出模式"><el-switch v-model="form.json_mode" /></el-form-item>
          <el-form-item label="分段字符数"><el-input-number v-model="form.chunk_chars" :min="1000" :max="1000000" :step="1000" /></el-form-item>
        </div>
      </section>
      <section class="panel">
        <h3>费用与上限</h3>
        <div class="model-grid three-cols">
          <el-form-item label="输入单价（元 / 百万 token）" prop="price_in_per_m"><el-input v-model="form.price_in_per_m" /></el-form-item>
          <el-form-item label="输出单价（元 / 百万 token）" prop="price_out_per_m"><el-input v-model="form.price_out_per_m" /></el-form-item>
          <el-form-item label="每月调用上限"><el-input-number v-model="form.monthly_call_cap" :min="0" :max="1000000000" /><span class="hint note">0 表示不限</span></el-form-item>
          <el-form-item label="个人每日上限"><el-input-number v-model="form.user_daily_cap" :min="0" :max="10000" /><span class="hint note">按上海时间每天统计，0 表示不限</span></el-form-item>
        </div>
        <div class="actions"><el-button type="primary" :loading="saving" @click="save">保存</el-button></div>
      </section>
    </el-form>
  </div>
</template>

<style scoped>
h3 { margin: 0 0 16px; font-size: 18px; font-weight: 600; }
.model-form { margin-top: 16px; }
.model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; }
.three-cols { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.span-all { grid-column: 1 / -1; }
.model-grid :deep(.el-form-item) { min-width: 0; }
.model-grid :deep(.el-input-number) { width: 100%; }
.usage-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.stat-value { overflow-wrap: anywhere; }
.stat-value small { font-size: 12px; font-weight: 400; color: var(--el-text-color-secondary); }
.note { width: 100%; margin-top: 4px; line-height: 1.5; }
.actions { border-top: 1px solid var(--el-border-color-lighter); padding-top: 20px; }
.actions > .hint { margin-right: 12px; }
.actions > .unsaved { color: var(--el-color-warning); }
.extra-note { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
</style>
