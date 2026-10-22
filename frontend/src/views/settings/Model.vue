<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { adminModelApi, type ModelSettings, type ModelTestResult, type UsageOut } from '@/api/admin'
import { ApiError } from '@/api/http'

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
  })
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
  <div class="page">
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />

    <el-card v-loading="loading" shadow="never">
      <h3 class="first">模型接口</h3>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="140px" @submit.prevent="save">
        <el-form-item label="接口地址" prop="base_url">
          <el-input v-model="form.base_url" placeholder="例如 https://api.deepseek.com/v1" />
          <div class="hint note">更换地址后，需 IT 同步修改虚拟机防火墙白名单。</div>
        </el-form-item>
        <el-form-item label="模型名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="API 密钥" prop="api_key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            autocomplete="new-password"
            :placeholder="saved?.api_key_set ? `已设置（末 4 位 ${saved.api_key_last4}），留空表示不修改` : '尚未设置'"
          />
        </el-form-item>
        <el-form-item label="附加请求参数" prop="extra_text">
          <el-input v-model="form.extra_text" type="textarea" :rows="3" />
          <div class="hint note">JSON 对象。DeepSeek 推荐值：{"thinking": {"type": "enabled"}}</div>
        </el-form-item>

        <h3>调用参数</h3>
        <el-form-item label="温度（0–2）">
          <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" :precision="2" />
        </el-form-item>
        <el-form-item label="最大输出 token">
          <el-input-number v-model="form.max_tokens" :min="1" :max="1000000" :step="1000" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="form.timeout_s" :min="10" :max="600" />
        </el-form-item>
        <el-form-item label="JSON 输出模式">
          <el-switch v-model="form.json_mode" />
        </el-form-item>
        <el-form-item label="分段字符数">
          <el-input-number v-model="form.chunk_chars" :min="1000" :max="1000000" :step="1000" />
        </el-form-item>

        <h3>费用与上限</h3>
        <el-form-item label="输入单价（元 / 百万 token）" prop="price_in_per_m">
          <el-input v-model="form.price_in_per_m" class="narrow" />
        </el-form-item>
        <el-form-item label="输出单价（元 / 百万 token）" prop="price_out_per_m">
          <el-input v-model="form.price_out_per_m" class="narrow" />
        </el-form-item>
        <el-form-item label="每月调用上限">
          <el-input-number v-model="form.monthly_call_cap" :min="0" :max="1000000000" />
          <span class="hint inline">0 表示不限</span>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        show-icon
        :title="testResult.ok ? `连接成功：模型 ${testResult.model}，耗时 ${testResult.duration_ms} 毫秒` : `连接失败：${testResult.message}`"
      />
      <div class="actions">
        <span class="hint inline">测试的是已保存的设置</span>
        <el-button :loading="testing" @click="testConnection">测试连接</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </div>
    </el-card>

    <el-card class="gap" shadow="never">
      <h3 class="first">本月用量{{ usage ? `（${usage.month}）` : '' }}</h3>
      <el-descriptions v-if="usage" border size="small" :column="4">
        <el-descriptions-item label="调用次数">
          {{ usage.calls }}{{ usage.cap > 0 ? ` / ${usage.cap}` : '（不限）' }}
        </el-descriptions-item>
        <el-descriptions-item label="输入 token">{{ usage.tokens_in }}</el-descriptions-item>
        <el-descriptions-item label="输出 token">{{ usage.tokens_out }}</el-descriptions-item>
        <el-descriptions-item label="估算费用（元）">{{ usage.cost }}</el-descriptions-item>
      </el-descriptions>
      <el-empty v-else description="暂时无法读取用量" :image-size="60" />
    </el-card>
  </div>
</template>

<style scoped>
h3 {
  margin: 20px 0 8px;
  font-size: 15px;
  font-weight: bold;
}
h3.first {
  margin-top: 0;
}
.narrow {
  width: 200px;
}
.note {
  width: 100%;
  margin-top: 4px;
  line-height: 1.5;
}
.inline {
  margin: 0 12px;
}
</style>
