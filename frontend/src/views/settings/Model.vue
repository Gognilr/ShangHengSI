<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@lucide/vue'
import { adminModelApi, type ModelBody, type ModelList, type ModelRow, type ModelTests } from '@/api/admin'
import { ApiError } from '@/api/http'
import PageHeader from '@/components/PageHeader.vue'
import SettingSection from '@/components/SettingSection.vue'
import ModelEditDrawer from '@/components/ModelEditDrawer.vue'
import RowMore from '@/components/RowMore.vue'
import SaveState from '@/components/SaveState.vue'
import { useAutoSave } from '@/utils/autosave'

const data = ref<ModelList | null>(null)
const loading = ref(false)
const error = ref('')
const dailyCap = ref(0)
const busy = ref<number | null>(null)
const editing = ref<ModelRow | null>(null)
const drawer = ref(false)
const results = ref<Record<number, ModelTests>>({})
const capDirty = computed(() => data.value !== null && typeof dailyCap.value === 'number' && dailyCap.value !== data.value.user_daily_cap)
const capSave = useAutoSave(saveCap)
watch(dailyCap, () => { if (capDirty.value) capSave.schedule() })

// 预置厂商（V31-U5F-06 16）：接口地址与推荐模型名已填好，用户只填 API 密钥；保存时由后端先测连接与结构化输出，通过才创建并启用
interface Preset { id: string; name: string; description: string; base_url: string; model_name: string; max_tokens: number }
const PRESETS: Preset[] = [
  { id: 'deepseek', name: 'DeepSeek', description: '深度求索，性价比高，适合日常审核。', base_url: 'https://api.deepseek.com/v1', model_name: 'deepseek-chat', max_tokens: 8192 },
  { id: 'glm', name: '智谱 GLM', description: '智谱清言系列，长文本与中文表现稳定。', base_url: 'https://open.bigmodel.cn/api/paas/v4', model_name: 'glm-4.6', max_tokens: 16000 },
  { id: 'kimi', name: 'Kimi', description: '月之暗面，长上下文。', base_url: 'https://api.moonshot.cn/v1', model_name: 'kimi-k2-0905-preview', max_tokens: 16000 },
  { id: 'qwen', name: '通义千问', description: '阿里云百炼，兼容模式接口。', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_name: 'qwen-plus', max_tokens: 8192 },
]
const presetForm = reactive<Record<string, { base_url: string; model_name: string; api_key: string; busy: boolean; error: string }>>(
  Object.fromEntries(PRESETS.map(p => [p.id, { base_url: p.base_url, model_name: p.model_name, api_key: '', busy: false, error: '' }])))
// 已添加过同名模型的厂商不再显示预置卡片
const freePresets = computed(() => PRESETS.filter(p => !data.value?.items.some(row => row.display_name === p.name)))
async function addPreset(p: Preset) {
  const f = presetForm[p.id]!
  const key = f.api_key.trim()
  if (!/^[!-~]{8,500}$/.test(key)) { f.error = '请输入 8～500 个可见的英文字符的 API 密钥，不能含空格'; return }
  if (!/^https?:\/\/\S+$/.test(f.base_url.trim()) || !f.model_name.trim()) { f.error = '请填写接口地址（以 http:// 或 https:// 开头）和模型名称'; return }
  const body: ModelBody = { display_name: p.name, description: p.description, for_review: true, for_assist: true,
    base_url: f.base_url.trim(), model_name: f.model_name.trim(), api_key: key, temperature: 0.1, max_tokens: p.max_tokens,
    timeout_s: 180, json_mode: true, extra_params: {}, chunk_chars: 60000, price_in_per_m: '0', price_out_per_m: '0',
    monthly_call_cap: 0, sort_order: 0 }
  f.busy = true; f.error = ''
  try {
    await adminModelApi.create(body)
    f.api_key = ''
    ElMessage.success(`${p.name} 测试通过，已启用`)
    await load()
  } catch (e) { f.error = e instanceof ApiError ? e.message : '保存失败，请稍后重试' }
  finally { f.busy = false }
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    const next = await adminModelApi.list()
    // 刷新卡片不覆盖尚未保存的个人日上限。
    if (!capDirty.value) dailyCap.value = next.user_daily_cap
    data.value = next
  } catch (e) { error.value = e instanceof ApiError ? e.message : '加载失败，请刷新页面' }
  finally { loading.value = false }
}
onMounted(load)
async function saveCap() {
  if (!data.value || !capDirty.value) return
  const result = await adminModelApi.dailyCap(dailyCap.value)
  data.value.user_daily_cap = result.value
}
function edit(row: ModelRow | null) { editing.value = row; drawer.value = true }
function host(url: string) { try { return new URL(url).host } catch { return '接口地址无效' } }
async function operate(row: ModelRow, action: 'test' | 'review' | 'assist' | 'enabled' | 'delete') {
  if (busy.value !== null) return
  if (action === 'delete') {
    try { await ElMessageBox.confirm(`删除模型“${row.display_name}”后无法恢复。`, '删除模型', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
    catch { return }
  }
  busy.value = row.id
  try {
    if (action === 'test') {
      const result = await adminModelApi.test(row.id)
      results.value[row.id] = result.tests
      if (result.message) ElMessage.warning(result.message)
    } else if (action === 'review' || action === 'assist') await adminModelApi.setDefault(row.id, action)
    else if (action === 'enabled') await adminModelApi.setEnabled(row.id, !row.enabled)
    else await adminModelApi.delete(row.id)
    if (action !== 'test') ElMessage.success('已更新模型设置')
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试') }
  finally { busy.value = null }
}
</script>

<template>
  <div class="settings-page">
    <PageHeader title="模型接口" description="管理审核与助手使用的模型、调用参数和费用上限" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="gap"><el-button link type="primary" @click="load">重新加载</el-button></el-alert>
    <div v-loading="loading" class="panel">
      <SettingSection title="全部模型" description="个人每日调用上限跨模型合计，按上海时间每天统计；按实际调用模型的次数计，一份长文档分段审核或格式重试会计多次。" stacked>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="个人每日调用上限"><div class="cap-controls"><el-input-number v-model="dailyCap" :min="0" :precision="0" :disabled="!data" aria-label="个人每日调用上限" @blur="capSave.flush()" /><span class="hint">0 表示不限</span><SaveState :status="capSave.status.value" :message="capSave.message.value" @retry="capSave.retry()" /></div></el-form-item>
        </el-form>
        <div v-if="data" class="usage-stats">
          <div class="stat"><div class="stat-label">本月调用次数</div><div class="stat-value">{{ data.usage.calls }}</div></div>
          <div class="stat"><div class="stat-label">本月估算费用（元）</div><div class="stat-value">{{ data.usage.cost }}</div></div>
        </div>
      </SettingSection>
      <div v-if="data" class="model-grid">
        <article v-for="row in data.items" :key="row.id" class="model-card" :class="{ disabled: !row.enabled }">
          <h2>{{ row.display_name }}</h2>
          <p class="connection">{{ row.model_name }} · {{ host(row.base_url) }}</p>
          <p v-if="row.description" class="description">{{ row.description }}</p>
          <div class="tags">
            <el-tag v-if="row.is_default_review" size="small">审核默认</el-tag><el-tag v-if="row.is_default_assist" size="small">助手默认</el-tag>
            <el-tag v-if="row.for_review && !row.for_assist" size="small" type="info">仅审核</el-tag><el-tag v-if="row.for_assist && !row.for_review" size="small" type="info">仅助手</el-tag>
            <el-tag v-if="!row.enabled" size="small" type="info">已停用</el-tag><el-tag v-if="!row.structured_ok" size="small" type="warning">结构化未通过</el-tag>
          </div>
          <p class="model-usage num">调用 {{ row.usage.calls }} 次 · 约 ¥{{ row.usage.cost }} / 上限 {{ row.monthly_call_cap > 0 ? `${row.monthly_call_cap} 次` : '不限' }}</p>
          <div v-if="results[row.id]" class="test-results" aria-live="polite">
            <el-alert :title="results[row.id]!.connection.ok ? '连接测试：成功' : `连接测试：${results[row.id]!.connection.message}`" :type="results[row.id]!.connection.ok ? 'success' : 'error'" :closable="false" />
            <el-alert :title="results[row.id]!.structured.ok ? '结构化输出测试：成功' : `结构化输出测试：${results[row.id]!.structured.message}`" :type="results[row.id]!.structured.ok ? 'success' : 'warning'" :closable="false" />
          </div>
          <div class="card-actions">
            <el-button link type="primary" :disabled="busy !== null" @click="edit(row)">编辑</el-button>
            <el-button link type="primary" :loading="busy === row.id" :disabled="busy !== null && busy !== row.id" @click="operate(row, 'test')">测试连接</el-button>
            <RowMore :disabled="busy !== null">
              <el-dropdown-item :disabled="!row.enabled || !row.for_review || row.is_default_review" @click="operate(row, 'review')">设为审核默认</el-dropdown-item>
              <el-dropdown-item :disabled="!row.enabled || !row.for_assist || row.is_default_assist" @click="operate(row, 'assist')">设为助手默认</el-dropdown-item>
              <el-dropdown-item :disabled="row.enabled && (row.is_default_review || row.is_default_assist)" @click="operate(row, 'enabled')">{{ row.enabled ? '停用' : '启用' }}{{ row.enabled && (row.is_default_review || row.is_default_assist) ? '（默认模型）' : '' }}</el-dropdown-item>
              <el-dropdown-item v-if="!row.used" divided class="delete-item" @click="operate(row, 'delete')">删除</el-dropdown-item>
            </RowMore>
          </div>
        </article>
        <article v-for="p in freePresets" :key="p.id" class="model-card preset-card">
          <h2>{{ p.name }}</h2>
          <p class="description">{{ p.description }}</p>
          <el-form label-position="top" size="small" @submit.prevent="addPreset(p)">
            <el-form-item label="API 密钥"><el-input v-model="presetForm[p.id]!.api_key" type="password" show-password autocomplete="new-password" maxlength="500" :disabled="presetForm[p.id]!.busy" :aria-label="`${p.name} API 密钥`" /></el-form-item>
            <el-form-item label="模型名称"><el-input v-model="presetForm[p.id]!.model_name" maxlength="100" :disabled="presetForm[p.id]!.busy" /></el-form-item>
            <el-form-item label="接口地址"><el-input v-model="presetForm[p.id]!.base_url" maxlength="500" :disabled="presetForm[p.id]!.busy" /></el-form-item>
          </el-form>
          <p class="hint">网络出口须放行 {{ host(presetForm[p.id]!.base_url) }}，否则测试会失败。</p>
          <el-alert v-if="presetForm[p.id]!.error" :title="presetForm[p.id]!.error" type="error" :closable="false" show-icon />
          <div class="card-actions"><el-button type="primary" :loading="presetForm[p.id]!.busy" :disabled="busy !== null" @click="addPreset(p)">{{ presetForm[p.id]!.busy ? '正在测试…' : '测试并启用' }}</el-button></div>
        </article>
        <button class="model-card add-card" :disabled="data.items.length >= 10 || busy !== null" @click="edit(null)"><Plus :size="16" :stroke-width="1.75" /><span>自定义模型</span><span class="hint">{{ data.items.length >= 10 ? '已达 10 个模型上限（含停用）' : `其他兼容 OpenAI 接口的模型 · 已配置 ${data.items.length} / 10` }}</span></button>
      </div>
    </div>
    <ModelEditDrawer v-model="drawer" :model="editing" @saved="load" />
  </div>
</template>

<style scoped>
.cap-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.usage-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.model-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }
.model-card { min-width: 0; padding: 20px; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); background: var(--el-bg-color); display: flex; flex-direction: column; gap: 12px; overflow-wrap: anywhere; }
.model-card.disabled { opacity: 0.6; }
h2 { font: var(--si-font-h4); margin: 0; }
p { margin: 0; }
.connection, .model-usage { font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.description { font: var(--si-font-body); }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.card-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
.test-results { display: grid; gap: 8px; }
.preset-card :deep(.el-form-item) { margin-bottom: 8px; }
.add-card { border-style: dashed; align-items: center; justify-content: center; min-height: 200px; font: var(--si-font-h4); color: var(--el-color-primary); cursor: pointer; }
.add-card:disabled { color: var(--el-text-color-secondary); cursor: not-allowed; }
.delete-item { color: var(--el-color-danger); }
</style>
