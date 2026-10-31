<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { adminModelApi, type ModelBody, type ModelRow, type ModelTests } from '@/api/admin'
import { ApiError } from '@/api/http'
import { useUnsaved } from '@/utils/unsaved'

const props = defineProps<{ modelValue: boolean; model: ModelRow | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; saved: [] }>()
const formRef = ref<FormInstance>()
const saving = ref(false)
const error = ref('')
const tests = ref<ModelTests | null>(null)
const editingId = ref<number | null>(null)
const keyLast4 = ref('')
const baseline = ref('')
const form = reactive({ display_name: '', description: '', for_review: true, for_assist: true,
  base_url: '', api_key: '', model_name: '', temperature: 0.1, max_tokens: 32000, timeout_s: 180,
  json_mode: true, extra_text: '{}', chunk_chars: 60000, price_in_per_m: '0', price_out_per_m: '0',
  monthly_call_cap: 0, sort_order: 0 })
function fill(row: ModelRow | null) {
  editingId.value = row?.id ?? null
  keyLast4.value = row?.api_key_last4 ?? ''
  Object.assign(form, { display_name: row?.display_name ?? '', description: row?.description ?? '',
    for_review: row?.for_review ?? true, for_assist: row?.for_assist ?? true,
    base_url: row?.base_url ?? '', api_key: '', model_name: row?.model_name ?? '',
    temperature: Number(row?.temperature ?? 0.1), max_tokens: row?.max_tokens ?? 32000,
    timeout_s: row?.timeout_s ?? 180, json_mode: row?.json_mode ?? true,
    extra_text: JSON.stringify(row?.extra_params ?? {}, null, 2), chunk_chars: row?.chunk_chars ?? 60000,
    price_in_per_m: row?.price_in_per_m ?? '0', price_out_per_m: row?.price_out_per_m ?? '0',
    monthly_call_cap: row?.monthly_call_cap ?? 0, sort_order: row?.sort_order ?? 0 })
  baseline.value = JSON.stringify(form)
}
watch(() => props.modelValue, open => { if (open) { fill(props.model); error.value = ''; tests.value = null; formRef.value?.clearValidate() } })
const dirty = computed(() => props.modelValue && baseline.value !== JSON.stringify(form))
useUnsaved([{ dirty: () => dirty.value || saving.value, save: async () => { await save() }, discard: () => fill(props.model) }])
async function close(done?: () => void) {
  if (saving.value) return
  if (dirty.value) {
    try { await ElMessageBox.confirm('有未保存的修改，关闭后将丢失。', '未保存的修改', { type: 'warning', confirmButtonText: '关闭', cancelButtonText: '继续编辑' }) }
    catch { return }
  }
  form.api_key = ''
  emit('update:modelValue', false)
  done?.()
}
function extra() {
  try {
    const value: unknown = JSON.parse(form.extra_text || '{}')
    if (value && typeof value === 'object' && !Array.isArray(value) && !('messages' in value)) return value as Record<string, unknown>
  } catch { /* 由表单显示格式错误 */ }
  return null
}
const rules: FormRules = {
  display_name: [{ required: true, whitespace: true, message: '请输入显示名称', trigger: 'blur' }],
  model_name: [{ required: true, whitespace: true, message: '请输入模型名称', trigger: 'blur' }],
  base_url: [{ required: true, message: '请输入接口地址', trigger: 'blur' }, { pattern: /^https?:\/\/\S+$/, message: '须以 http:// 或 https:// 开头，不能含空格', trigger: 'blur' }],
  api_key: [{ validator: (_r, value: string, cb) => {
    const v = value.trim()
    cb((v === '' && editingId.value !== null) || /^[\x21-\x7e]{8,500}$/.test(v) ? undefined : new Error('请输入 8～500 个可见的英文字符，不能含空格'))
  }, trigger: 'blur' }],
  extra_text: [{ validator: (_r, _v, cb) => cb(extra() ? undefined : new Error('须为 JSON 对象，且不能包含 messages')), trigger: 'blur' }],
  price_in_per_m: [{ pattern: /^\d{1,8}(\.\d{1,4})?$/, message: '请输入不小于 0、最多 4 位小数的单价', trigger: 'blur' }],
  price_out_per_m: [{ pattern: /^\d{1,8}(\.\d{1,4})?$/, message: '请输入不小于 0、最多 4 位小数的单价', trigger: 'blur' }],
}
async function save() {
  if (saving.value || !await formRef.value?.validate().catch(() => false)) return
  const params = extra()
  if (!params) return
  const body: ModelBody = { display_name: form.display_name.trim(), description: form.description.trim() || null,
    for_review: form.for_review, for_assist: form.for_assist, base_url: form.base_url.trim(),
    model_name: form.model_name.trim(), api_key: form.api_key.trim() || undefined,
    temperature: form.temperature, max_tokens: form.max_tokens, timeout_s: form.timeout_s,
    json_mode: form.json_mode, extra_params: params, chunk_chars: form.chunk_chars,
    price_in_per_m: form.price_in_per_m.trim(), price_out_per_m: form.price_out_per_m.trim(),
    monthly_call_cap: form.monthly_call_cap, sort_order: form.sort_order }
  saving.value = true
  error.value = ''
  tests.value = null
  try {
    const row = editingId.value === null ? await adminModelApi.create(body) : await adminModelApi.update(editingId.value, body)
    fill(row)
    tests.value = row.tests ?? null
    emit('saved')
    ElMessage.success('模型已保存')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '保存失败，请稍后重试'
    if (e instanceof ApiError) tests.value = (e.data as { tests?: ModelTests } | undefined)?.tests ?? null
  } finally { saving.value = false }
}
</script>

<template>
  <el-drawer :model-value="modelValue" :title="editingId === null ? '新增模型' : '编辑模型'" size="560px" :before-close="close" :close-on-click-modal="!saving" :close-on-press-escape="!saving">
    <el-alert title="每增加一个模型就是一个新的外发目的地，接口地址须在公司出口放行。" type="warning" :closable="false" show-icon class="gap" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="gap" />
    <div v-if="tests" class="test-results gap" aria-live="polite">
      <el-alert :title="tests.connection.ok ? '连接测试：成功' : `连接测试：${tests.connection.message}`" :type="tests.connection.ok ? 'success' : 'error'" :closable="false" show-icon />
      <el-alert :title="tests.structured.ok ? '结构化输出测试：成功' : `结构化输出测试：${tests.structured.message}`" :type="tests.structured.ok ? 'success' : 'warning'" :closable="false" show-icon />
    </div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving" @submit.prevent="save">
      <el-form-item label="显示名称" prop="display_name"><el-input v-model="form.display_name" maxlength="40" show-word-limit /></el-form-item>
      <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" maxlength="200" show-word-limit /></el-form-item>
      <div class="purposes"><el-checkbox v-model="form.for_review">可用于智能审核</el-checkbox><el-checkbox v-model="form.for_assist">可用于智能助手</el-checkbox></div>
      <el-form-item label="接口地址" prop="base_url"><el-input v-model="form.base_url" maxlength="500" placeholder="https://api.example.com/v1" /></el-form-item>
      <el-form-item label="API 密钥" prop="api_key"><el-input v-model="form.api_key" type="password" show-password autocomplete="new-password" :placeholder="keyLast4 ? `已设置（末 4 位 ${keyLast4}），留空不修改` : '请输入 API 密钥'" maxlength="500" /></el-form-item>
      <el-form-item label="模型名称" prop="model_name"><el-input v-model="form.model_name" maxlength="100" /></el-form-item>
      <div class="fields">
        <el-form-item label="温度（0～2）"><el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" :precision="2" /></el-form-item>
        <el-form-item label="最大输出 token"><el-input-number v-model="form.max_tokens" :min="1" :max="1000000" /></el-form-item>
        <el-form-item label="超时（秒）"><el-input-number v-model="form.timeout_s" :min="10" :max="600" /></el-form-item>
        <el-form-item label="JSON 输出模式"><el-switch v-model="form.json_mode" /></el-form-item>
      </div>
      <el-form-item label="附加请求参数" prop="extra_text"><el-input v-model="form.extra_text" type="textarea" :rows="4" /><span class="hint">JSON 对象，不能包含 messages。</span></el-form-item>
      <div class="fields">
        <el-form-item label="分段字符数"><el-input-number v-model="form.chunk_chars" :min="1000" :max="1000000" /></el-form-item>
        <el-form-item label="每月调用上限"><el-input-number v-model="form.monthly_call_cap" :min="0" :max="1000000000" /><span class="hint">0 表示不限</span></el-form-item>
        <el-form-item label="输入单价（元 / 百万 token）" prop="price_in_per_m"><el-input v-model="form.price_in_per_m" /></el-form-item>
        <el-form-item label="输出单价（元 / 百万 token）" prop="price_out_per_m"><el-input v-model="form.price_out_per_m" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="form.sort_order" :precision="0" /></el-form-item>
      </div>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="close()">关闭</el-button><el-button type="primary" :loading="saving" @click="save">{{ saving ? '正在测试并保存…' : '保存模型' }}</el-button></template>
  </el-drawer>
</template>

<style scoped>
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.fields :deep(.el-input-number) { width: 100%; }
.fields :deep(.el-form-item) { min-width: 0; }
.purposes { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.test-results { display: grid; gap: 8px; }
.hint { display: block; width: 100%; }
</style>
