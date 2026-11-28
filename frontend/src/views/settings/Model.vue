<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Activity, ChevronDown, ChevronUp, CircleCheck, CircleDashed, CircleX, KeyRound, ListChecks, LoaderCircle, MessageSquare, Plug, Plus, Replace, RotateCcw, Sparkle } from '@lucide/vue'
import { adminModelApi, type ModelBalance, type ModelBody, type ModelList, type ModelOverview, type ModelRow, type ModelTests } from '@/api/admin'
import { ApiError } from '@/api/http'
import SiBadge from '@/components/ui/SiBadge.vue'
import SiNotice from '@/components/ui/SiNotice.vue'
import SiRowCard from '@/components/ui/SiRowCard.vue'
import SiSpark, { type SparkDay } from '@/components/ui/SiSpark.vue'
import SiRing from '@/components/ui/SiRing.vue'
import SiStack, { type StackSeg } from '@/components/ui/SiStack.vue'
import ExitMap from '@/components/settings/ExitMap.vue'
import ModelEditDrawer from '@/components/ModelEditDrawer.vue'
import ModelPriceInfo from '@/components/ModelPriceInfo.vue'
import SaveState from '@/components/SaveState.vue'
import { formatShort } from '@/utils/time'
import { useAutoSave } from '@/utils/autosave'

/**
 * 设置 · 模型（澄明二版 6.20，V3.6 样板页）：出口地图、值班席位、已接入、接入新模型四块。
 * 计价与余额对账沿用 V351Q-21，只改展示。
 */
const data = ref<ModelList | null>(null)
const ov = ref<ModelOverview | null>(null)
const loading = ref(false)
const error = ref('')
const busy = ref<number | null>(null)
const editing = ref<ModelRow | null>(null)
const drawer = ref(false)
const map = ref<InstanceType<typeof ExitMap>>()
const open = reactive<Record<number, boolean>>({})
const fresh = ref<number | null>(null)

async function load() {
  loading.value = !data.value
  error.value = ''
  try {
    const [next, o] = await Promise.all([adminModelApi.list(), adminModelApi.overview()])
    if (!capDirty.value) dailyCap.value = next.user_daily_cap
    data.value = next
    ov.value = o
  } catch (e) { error.value = e instanceof ApiError ? e.message : '加载失败，请刷新页面' }
  finally { loading.value = false }
}
onMounted(load)
const items = computed(() => data.value?.items ?? [])
const monthLabel = computed(() => ov.value ? `${+ov.value.month.slice(5)} 月 · 截至今天` : '')

// ---- ① 第一关：个人每日调用上限，就地修改 ----
const dailyCap = ref(0)
const capOpen = ref(false)
const capDirty = computed(() => data.value !== null && typeof dailyCap.value === 'number' && dailyCap.value !== data.value.user_daily_cap)
const capSave = useAutoSave(saveCap)
watch(dailyCap, () => { if (capDirty.value) capSave.schedule() })
async function saveCap() {
  if (!data.value || !capDirty.value) return
  const result = await adminModelApi.dailyCap(dailyCap.value)
  data.value.user_daily_cap = result.value
  if (ov.value) ov.value.user_daily_cap = result.value
}

// ---- ② 值班席位 ----
type Purpose = 'review' | 'assist'
const SEATS: { purpose: Purpose; label: string; aux: string; icon: typeof ListChecks }[] = [
  { purpose: 'review', label: '审核默认', aux: '文件审核与标书补查', icon: ListChecks },
  { purpose: 'assist', label: '助手默认', aux: '对话、追问与云端翻译', icon: MessageSquare },
]
const seatModel = (p: Purpose) => items.value.find(m => p === 'review' ? m.is_default_review : m.is_default_assist)
// 菜单只列可用的模型；审核默认另要求结构化输出通过，未通过的灰显并写原因
const seatChoices = (p: Purpose) => items.value.filter(m => m.enabled && (p === 'review' ? m.for_review : m.for_assist))
  .map(m => ({ m, reason: p === 'review' && !m.structured_ok ? '结构化输出未通过' : '' }))
async function changeSeat(p: Purpose, m: ModelRow) {
  if (busy.value !== null || seatModel(p)?.id === m.id) return
  busy.value = m.id
  try {
    await adminModelApi.setDefault(m.id, p)
    await load()
    map.value?.flash(m.id)
    ElMessage.success(`${p === 'review' ? '审核' : '助手'}默认已换成 ${m.display_name}`)
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试') }
  finally { busy.value = null }
}

// ---- ③ 已接入 ----
const usage = (id: number) => ov.value?.models.find(m => m.id === id)
function host(url: string) { try { return new URL(url).host } catch { return '接口地址无效' } }
function spark(id: number): SparkDay[] {
  if (!ov.value) return []
  const [y, mo] = ov.value.month.split('-').map(Number) as [number, number]
  const by = new Map((usage(id)?.daily ?? []).map(d => [d.day, d]))
  return Array.from({ length: ov.value.days }, (_, i) => {
    const day = i + 1, wd = new Date(y, mo - 1, day).getDay(), d = by.get(day)
    const extra = d && (d.failed || d.blocked) ? `（失败 ${d.failed}、拦截 ${d.blocked}）` : ''
    return { value: d?.calls ?? 0, label: `${mo} 月 ${day} 日 ${d?.calls ?? 0} 次${extra}`, weekend: wd === 0 || wd === 6, future: day > ov.value!.today }
  })
}
function sparkLine(id: number) {
  const u = usage(id)
  if (!u || u.calls + u.failed === 0) return '本月没有调用'
  const med = u.median_ms === null ? '—' : u.median_ms >= 1000 ? `${(u.median_ms / 1000).toFixed(1)} s` : `${u.median_ms} ms`
  return `中位 ${med} · 成功 ${(u.calls / (u.calls + u.failed) * 100).toFixed(1)}%`
}
function costText(row: ModelRow) {
  const u = row.usage
  if (u.calls > 0 && u.unpriced === u.calls) return '未收录价格'
  return `¥ ${u.cost}`
}
const PURPOSES: { key: string; label: string; color: string }[] = [
  { key: 'review', label: '审核', color: 'var(--si-m-review)' }, { key: 'chat', label: '追问', color: 'var(--si-brand-2)' },
  { key: 'assist', label: '助手', color: 'var(--si-ai)' }, { key: 'bid', label: '标书', color: 'var(--si-m-bid)' },
  { key: 'test', label: '测试', color: 'var(--si-ink-4)' },
]
const purposeSegs = (id: number): StackSeg[] => PURPOSES.map(p => ({ key: p.key, label: p.label, color: p.color, value: usage(id)?.purposes[p.key] ?? 0 }))
const wan = (n: number) => (n / 10000).toFixed(1)
function balanceText(b: ModelBalance) {
  if (b.error) return `余额查询失败：${b.error}（${formatShort(b.checked_at)}）`
  if (!b.checked_at || b.balance === null) return '每小时查询一次账户余额，用实际扣费核对价格表；尚未查询。'
  const head = `账户余额 ¥${b.balance}（${formatShort(b.checked_at)}）。`
  const since = `自 ${formatShort(b.base_at)} 起实际扣费 ¥${b.spent}，按价格表 ¥${b.cost}`
  if (b.verdict === 'ok') return `${head}对账一致：${since}。`
  if (b.verdict === 'drift') return `${head}${since}，相差 ${b.diff}%。可能是厂商调价，或同一账户还有别的程序在用。`
  if (b.verdict === 'unpriced') return `${head}账户内有未收录价格的调用，无法对账。`
  const prev = b.prev ? `上一周期${b.prev.verdict === 'ok' ? '对账一致' : `相差 ${b.prev.diff}%`}；` : ''
  return `${head}${prev}本周期扣费不足 2 元，暂不判断。`
}
function drift(b: ModelBalance | null) { return !!b && (b.verdict === 'drift' || (b.verdict === 'pending' && b.prev?.verdict === 'drift')) }

// 体检：接通与结构化输出两步，逐步打勾；地图上的光点从网关走到这个节点
const probes = reactive<Record<number, { running: boolean; tests: ModelTests | null; at: string }>>({})
async function probe(row: ModelRow) {
  if (busy.value !== null) return
  busy.value = row.id
  probes[row.id] = { running: true, tests: null, at: '' }
  map.value?.ping(row.id)
  try {
    const result = await adminModelApi.test(row.id)
    probes[row.id] = { running: false, tests: result.tests, at: new Date().toISOString() }
    if (result.message) ElMessage.warning(result.message)
    await load()
  } catch (e) {
    delete probes[row.id]
    ElMessage.error(e instanceof ApiError ? e.message : '体检失败，请稍后重试')
  } finally { busy.value = null }
}
const seconds = (ms?: number) => ms === undefined ? '' : ` ${(ms / 1000).toFixed(1)} s`

function edit(row: ModelRow | null) { editing.value = row; drawer.value = true }
async function toggleEnabled(row: ModelRow) {
  if (busy.value !== null) return
  busy.value = row.id
  try { await adminModelApi.setEnabled(row.id, !row.enabled); ElMessage.success(row.enabled ? '已停用' : '已启用'); await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试') }
  finally { busy.value = null }
}
async function remove(row: ModelRow) {
  try { await ElMessageBox.confirm(`删除模型“${row.display_name}”后无法恢复。`, '删除模型', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try { await adminModelApi.delete(row.id); ElMessage.success('已删除'); await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '删除失败，请稍后重试') }
}
const isDefault = (row: ModelRow) => row.is_default_review || row.is_default_assist

// ---- ④ 接入新模型 ----
// 预置厂商（V31-U5F-06 16）：接口地址与推荐模型名已填好，用户只填 API 密钥；保存时由后端先测接通与结构化输出，通过才创建并启用。
// 推荐模型名取各家当前在售、已收录价格表的型号（V351Q-21）
interface Preset { id: string; name: string; vendor: string; glyph: string; description: string; base_url: string; model_name: string; max_tokens: number }
const PRESETS: Preset[] = [
  { id: 'deepseek', name: 'DeepSeek', vendor: '深度求索', glyph: 'D', description: '深度求索，性价比高，适合日常审核。', base_url: 'https://api.deepseek.com', model_name: 'deepseek-flash', max_tokens: 32000 },
  { id: 'glm', name: '智谱 GLM', vendor: '智谱', glyph: 'G', description: '智谱清言系列，长文本与中文表现稳定。', base_url: 'https://open.bigmodel.cn/api/paas/v4', model_name: 'glm-5.3', max_tokens: 16000 },
  { id: 'kimi', name: 'Kimi', vendor: '月之暗面', glyph: 'K', description: '月之暗面，长上下文。', base_url: 'https://api.moonshot.cn/v1', model_name: 'kimi-k3.1-pro', max_tokens: 16000 },
  { id: 'qwen', name: '通义千问', vendor: '阿里云百炼', glyph: 'Q', description: '阿里云百炼，兼容模式接口。', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model_name: 'qwen-plus', max_tokens: 8192 },
]
const freePresets = computed(() => PRESETS.filter(p => !items.value.some(row => row.display_name === p.name)))
const full = computed(() => items.value.length >= 10)
const picked = ref<Preset | null>(null)
const form = reactive({ api_key: '', base_url: '', model_name: '', more: false, error: '', step: -1, failed: -1 })
const STEPS = ['接通', '结构化输出', '启用']
function pick(p: Preset) {
  if (full.value) return
  if (picked.value?.id === p.id) { picked.value = null; return }
  picked.value = p
  Object.assign(form, { api_key: '', base_url: p.base_url, model_name: p.model_name, more: false, error: '', step: -1, failed: -1 })
}
async function enable() {
  const p = picked.value
  if (!p || form.step >= 0 && form.failed < 0) return
  const key = form.api_key.trim()
  if (!/^[!-~]{8,500}$/.test(key)) { form.error = '请输入 8～500 个可见的英文字符的 API 密钥，不能含空格'; return }
  if (!/^https?:\/\/\S+$/.test(form.base_url.trim()) || !form.model_name.trim()) { form.error = '请填写接口地址（以 http:// 或 https:// 开头）和模型名称'; return }
  const body: ModelBody = { display_name: p.name, description: p.description, for_review: true, for_assist: true,
    base_url: form.base_url.trim(), model_name: form.model_name.trim(), api_key: key, temperature: 0.1, max_tokens: p.max_tokens,
    timeout_s: 180, json_mode: true, extra_params: {}, chunk_chars: 60000, monthly_call_cap: 0, sort_order: 0 }
  form.error = ''; form.failed = -1; form.step = 0
  // 后端一次请求依次测接通与结构化输出，通过才创建；这里按请求进度逐步显示
  const t = setTimeout(() => { if (form.step === 0) form.step = 1 }, 900)
  try {
    const row = await adminModelApi.create(body)
    clearTimeout(t)
    form.step = 2
    await load()
    form.step = 3
    fresh.value = row.id
    setTimeout(() => { picked.value = null; if (fresh.value === row.id) fresh.value = null }, 1600)
    ElMessage.success(`${p.name} 测试通过，已启用`)
  } catch (e) {
    clearTimeout(t)
    const tests = e instanceof ApiError ? (e.data as { tests?: ModelTests } | undefined)?.tests : undefined
    form.failed = tests && !tests.connection.ok ? 0 : tests ? 1 : form.step
    form.error = e instanceof ApiError ? e.message : '保存失败，请稍后重试'
  }
}
const stepState = (i: number) => form.failed === i ? 'no' : i < form.step ? 'ok' : i === form.step && form.failed < 0 ? 'run' : ''
</script>

<template>
  <div class="settings-page model-page">
    <header class="mp-head">
      <h2>模型</h2>
      <p>云端模型只收到遮住名称后的文字。每次调用都经过模型网关，并记录在案。</p>
    </header>
    <SiNotice v-if="error" type="error" :title="error"><template #actions><el-button size="small" @click="load">重新加载</el-button></template></SiNotice>

    <template v-if="data">
      <!-- ① 出口地图 -->
      <section class="card xmap" aria-labelledby="xm-t">
        <div class="row"><h3 id="xm-t">出口地图</h3><span class="aux">{{ monthLabel }}</span>
          <el-button class="ml-auto" text size="small" @click="map?.replay()"><RotateCcw :size="14" :stroke-width="1.75" />重放</el-button></div>
        <ExitMap ref="map" :overview="ov" :models="items" @cap="capOpen = !capOpen" />
        <div v-if="capOpen" class="cap-pop" role="dialog" aria-label="修改个人每日调用上限" @keydown.esc="capOpen = false">
          <b>个人每日调用上限</b>
          <div class="cap-row"><el-input-number v-model="dailyCap" :min="0" :precision="0" size="small" aria-label="个人每日调用上限" @blur="capSave.flush()" /><span class="aux">次 · 0 表示不限</span></div>
          <p class="aux">跨模型合计，按上海时间每天统计；长文档分段审核或格式重试按一轮计一次。</p>
          <div class="cap-row"><SaveState :status="capSave.status.value" :message="capSave.message.value" @retry="capSave.retry()" /><el-button class="ml-auto" size="small" @click="capSave.flush(); capOpen = false">完成</el-button></div>
        </div>
      </section>

      <!-- ② 值班席位 -->
      <div class="sec-t"><h3>值班席位</h3><span class="aux">默认模型不能直接停用，请先更换</span></div>
      <div class="duty">
        <div v-for="s in SEATS" :key="s.purpose" class="card seat">
          <span class="si-mod-icon seat-ic"><component :is="s.icon" :size="20" :stroke-width="1.75" /></span>
          <div class="grow"><span class="aux">{{ s.label }}</span><b>{{ seatModel(s.purpose)?.display_name ?? '尚未设置' }}</b><span class="aux">{{ s.aux }}</span></div>
          <el-dropdown trigger="click" :disabled="busy !== null" @command="(m: ModelRow) => changeSeat(s.purpose, m)">
            <el-button size="small"><Replace :size="14" :stroke-width="1.75" />更换</el-button>
            <template #dropdown><el-dropdown-menu class="seat-menu">
              <el-dropdown-item v-for="c in seatChoices(s.purpose)" :key="c.m.id" :command="c.m" :disabled="!!c.reason">
                <Sparkle :size="14" :stroke-width="1.75" class="t-ai" /><span class="grow">{{ c.m.display_name }}<small v-if="c.reason">{{ c.reason }}</small></span>
                <SiBadge v-if="seatModel(s.purpose)?.id === c.m.id" tone="ai">当前</SiBadge><SiBadge v-else-if="!c.reason" tone="ok">可用</SiBadge>
              </el-dropdown-item>
              <el-dropdown-item v-if="!seatChoices(s.purpose).length" disabled>没有可用的模型</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </div>

      <!-- ③ 已接入 -->
      <div class="sec-t"><h3>已接入</h3><SiBadge>{{ items.length }}</SiBadge><span v-if="full" class="aux">已达 10 个模型上限（含停用）</span></div>
      <div class="rows">
        <SiRowCard v-for="row in items" :key="row.id" v-model:open="open[row.id]" expandable class="mrow" :class="{ off: !row.enabled, fresh: fresh === row.id }">
          <template #icon><span class="si-mod-icon m-ic"><Sparkle :size="18" :stroke-width="1.75" /></span></template>
          <template #title><span class="nm">{{ row.display_name }}</span>
            <SiBadge v-if="row.is_default_review" tone="ai" :icon="Sparkle">审核默认</SiBadge><SiBadge v-if="row.is_default_assist" tone="ai">助手默认</SiBadge>
            <SiBadge v-if="row.for_review && !row.for_assist">仅审核</SiBadge><SiBadge v-if="row.for_assist && !row.for_review">仅助手</SiBadge>
            <SiBadge v-if="drift(row.balance)" tone="warn" plain title="余额对账与价格表相差较多">价格表可能已过期</SiBadge>
          </template>
          <template #sub>{{ row.model_name }} · {{ host(row.base_url) }} · 密钥 …{{ row.api_key_last4 }}
            <span v-if="probes[row.id]" class="chk" aria-live="polite">
              <template v-if="probes[row.id]!.running"><span class="run"><LoaderCircle :size="14" />接通</span><span class="run"><LoaderCircle :size="14" />结构化输出</span></template>
              <template v-else-if="probes[row.id]!.tests">
                <span :class="probes[row.id]!.tests!.connection.ok ? 'ok' : 'no'"><component :is="probes[row.id]!.tests!.connection.ok ? CircleCheck : CircleX" :size="14" />接通{{ probes[row.id]!.tests!.connection.ok ? seconds(probes[row.id]!.tests!.connection.duration_ms) : `：${probes[row.id]!.tests!.connection.message}` }}</span>
                <span v-if="!probes[row.id]!.tests!.connection.ok" class="skip"><CircleDashed :size="14" />结构化输出：未接通，未测</span>
                <span v-else :class="probes[row.id]!.tests!.structured.ok ? 'ok' : 'no'"><component :is="probes[row.id]!.tests!.structured.ok ? CircleCheck : CircleX" :size="14" />结构化输出{{ probes[row.id]!.tests!.structured.ok ? '' : `：${probes[row.id]!.tests!.structured.message}` }}</span>
                <span>刚刚</span>
              </template>
            </span>
          </template>
          <template #data>
            <div class="spk"><SiSpark :days="spark(row.id)" :color="row.enabled ? 'var(--si-ai)' : 'var(--si-ink-4)'" :aria-label="`${row.display_name} 本月每天调用`" /><span>{{ sparkLine(row.id) }}</span></div>
            <div class="use">
              <SiRing v-if="row.monthly_call_cap > 0" :value="row.usage.calls / row.monthly_call_cap * 100" :label="`本月 ${row.usage.calls} / ${row.monthly_call_cap} 次`" />
              <div><b>{{ row.usage.calls }}</b><span>次 · {{ costText(row) }}</span></div>
            </div>
          </template>
          <template #status>
            <SiBadge v-if="!row.enabled">已停用</SiBadge><SiBadge v-else-if="!row.structured_ok" tone="warn">结构化未通过</SiBadge><SiBadge v-else tone="ok">可用</SiBadge>
          </template>
          <template #actions>
            <el-button size="small" :loading="busy === row.id && !!probes[row.id]?.running" :disabled="busy !== null" @click="probe(row)"><Activity v-if="!(busy === row.id && probes[row.id]?.running)" :size="14" :stroke-width="1.75" />体检</el-button>
            <button type="button" class="ibtn" :aria-label="open[row.id] ? '收起' : '展开'" :aria-expanded="!!open[row.id]" @click="open[row.id] = !open[row.id]"><component :is="open[row.id] ? ChevronUp : ChevronDown" :size="16" :stroke-width="1.75" /></button>
          </template>
          <template #detail>
            <div class="x3">
              <div>
                <h5>体检</h5>
                <dl class="kv">
                  <dt>结构化输出</dt><dd>{{ row.structured_ok ? '通过' : '未通过' }}</dd>
                  <dt>上次</dt><dd>{{ row.structured_tested_at ? formatShort(row.structured_tested_at) : '尚未体检' }}</dd>
                  <dt>用于</dt><dd>{{ [row.for_review && '文件审核', row.for_assist && '助手'].filter(Boolean).join('、') || '—' }}</dd>
                </dl>
                <p v-if="row.balance && row.enabled" class="aux bal" :class="{ warn: drift(row.balance) }">{{ balanceText(row.balance) }}</p>
              </div>
              <div>
                <h5>本月用途</h5>
                <SiStack v-if="row.usage.calls" :segments="purposeSegs(row.id)" :pickable="false" />
                <p v-else class="aux">本月没有调用</p>
                <dl class="kv mt">
                  <dt>输入 token</dt><dd class="num">{{ wan(row.usage.tokens_in) }} 万<template v-if="row.usage.tokens_cached">（缓存 {{ wan(row.usage.tokens_cached) }} 万）</template></dd>
                  <dt>输出 token</dt><dd class="num">{{ wan(row.usage.tokens_out) }} 万</dd>
                  <dt v-if="row.usage.unpriced">未收录价格</dt><dd v-if="row.usage.unpriced">{{ row.usage.unpriced }} 次，只统计 token</dd>
                </dl>
              </div>
              <div>
                <h5>参数与单价</h5>
                <dl class="kv">
                  <dt>月上限</dt><dd>{{ row.monthly_call_cap > 0 ? `${row.monthly_call_cap} 次` : '不限' }}</dd>
                  <dt>温度 / 最大输出</dt><dd class="num">{{ row.temperature }} / {{ row.max_tokens }}</dd>
                  <dt>超时 / 分段</dt><dd class="num">{{ row.timeout_s }} 秒 / {{ row.chunk_chars }} 字</dd>
                </dl>
                <ModelPriceInfo class="mt" :price="row.price" />
                <div class="acts">
                  <el-button size="small" :disabled="busy !== null" @click="edit(row)">编辑</el-button>
                  <el-button size="small" text :disabled="busy !== null || (row.enabled && isDefault(row))" :title="row.enabled && isDefault(row) ? '默认模型不能停用，请先在值班席位更换' : undefined" @click="toggleEnabled(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
                  <el-button v-if="!row.used" size="small" text class="danger" :disabled="busy !== null" @click="remove(row)">删除</el-button>
                </div>
              </div>
            </div>
          </template>
        </SiRowCard>
      </div>

      <!-- ④ 接入新模型 -->
      <div class="sec-t"><h3>接入新模型</h3><span class="aux">只填 API 密钥，模型名与接口地址已按推荐值填好</span></div>
      <div class="tiles">
        <button v-for="p in freePresets" :key="p.id" type="button" class="tile" :class="{ on: picked?.id === p.id }" :disabled="full" :aria-expanded="picked?.id === p.id" @click="pick(p)">
          <span class="glyph">{{ p.glyph }}</span><b>{{ p.name }}</b><span class="aux">{{ p.vendor }}</span>
        </button>
        <button type="button" class="tile cus" :disabled="full || busy !== null" @click="picked = null; edit(null)">
          <span class="glyph"><Plus :size="16" :stroke-width="2" /></span><b>自定义模型</b><span class="aux">兼容 OpenAI 接口</span>
        </button>
      </div>
      <div v-if="picked" class="card connect">
        <div class="frm">
          <div class="fr"><label :for="`key-${picked.id}`">API 密钥</label>
            <div class="fx"><el-input :id="`key-${picked.id}`" v-model="form.api_key" type="password" show-password autocomplete="new-password" maxlength="500" :placeholder="`粘贴 ${picked.name} 的 API 密钥`" :disabled="form.step >= 0 && form.failed < 0" @keyup.enter="enable"><template #prefix><KeyRound :size="15" :stroke-width="1.75" /></template></el-input>
              <em>密钥加密保存，界面只显示尾号。</em></div></div>
          <div class="fr"><label>模型与接口</label>
            <div class="fx">
              <div v-if="!form.more" class="ro"><span class="num">{{ form.model_name }} · {{ host(form.base_url) }}</span><el-button text size="small" class="ml-auto" @click="form.more = true">修改</el-button></div>
              <template v-else>
                <el-input v-model="form.model_name" maxlength="100" aria-label="模型名称" placeholder="模型名称" />
                <el-input v-model="form.base_url" maxlength="500" aria-label="接口地址" placeholder="接口地址" />
              </template>
              <em>网络出口须放行 {{ host(form.base_url) }}，否则接通会失败。</em></div></div>
          <div class="fr"><label />
            <div class="fx"><div class="go">
              <el-button type="primary" :disabled="form.step >= 0 && form.failed < 0" @click="enable"><Plug :size="15" :stroke-width="1.75" />测试并启用</el-button>
              <ol v-if="form.step >= 0" class="steps3" aria-live="polite">
                <li v-for="(s, i) in STEPS" :key="s" :class="stepState(i)"><component :is="stepState(i) === 'ok' ? CircleCheck : stepState(i) === 'no' ? CircleX : stepState(i) === 'run' ? LoaderCircle : 'span'" :size="15" class="si" />{{ s }}</li>
              </ol>
            </div>
            <SiNotice v-if="form.error" type="error" :title="form.error" /></div></div>
        </div>
      </div>
    </template>
    <div v-else-if="loading" class="card skel si-wait" aria-busy="true">正在读取模型…</div>
    <ModelEditDrawer v-model="drawer" :model="editing" @saved="load" />
  </div>
</template>

<style scoped>
.model-page { display: flex; flex-direction: column; }
.mp-head h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.mp-head p, .aux { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.mp-head { margin-bottom: 16px; }
h3 { margin: 0; font: var(--si-font-h4); color: var(--si-ink-1); }
.card { background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.row { display: flex; align-items: center; gap: 12px; }
.ml-auto { margin-left: auto; }
.grow { flex: 1; min-width: 0; }
.mt { margin-top: 12px; }
.num { font-family: var(--si-font-mono); }
.t-ai { color: var(--si-ai); }
.xmap { position: relative; padding: 18px 22px 16px; }
.xmap .row { margin-bottom: 6px; }
.xmap :deep(.el-button svg) { margin-right: 4px; }
.cap-pop { position: absolute; z-index: 5; top: calc(50% + 62px); left: calc(22px + 27.5%); width: 300px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; background: var(--si-surface-pop); border: 1px solid var(--si-hair); border-radius: var(--si-radius-pop); box-shadow: var(--si-shadow-pop); }
.cap-pop b { font: var(--si-font-label); color: var(--si-ink-1); }
.cap-row { display: flex; align-items: center; gap: 10px; }
.sec-t { display: flex; align-items: center; gap: 10px; margin: 28px 0 12px; }
.duty { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.seat { display: flex; align-items: center; gap: 14px; padding: 16px 18px; }
.seat-ic { --mc: var(--si-ai); --mc-soft: var(--si-ai-soft); }
.seat b { display: block; font: var(--si-font-h4); color: var(--si-ink-1); }
.seat .aux { display: block; }
.seat :deep(.el-button svg), .mrow :deep(.el-button svg), .connect :deep(.el-button svg) { margin-right: 4px; }
.seat-menu :deep(.el-dropdown-menu__item) { gap: 8px; min-width: 220px; }
.seat-menu small { display: block; font: var(--si-font-caption); color: var(--si-ink-3); }
.rows { display: flex; flex-direction: column; gap: 10px; }
.m-ic { --mc: var(--si-ai); --mc-soft: var(--si-ai-soft); width: 40px; height: 40px; }
.mrow.off .m-ic { --mc: var(--si-ink-4); --mc-soft: var(--si-hover-bg); }
.mrow.off :deep(.rc-n b) { color: var(--si-ink-3); }
.nm { white-space: nowrap; }
.mrow.fresh { animation: si-rise-in var(--si-d4) var(--si-ease-out) both; box-shadow: 0 0 0 2px var(--si-ai-line), var(--si-shadow-2); }
.chk { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 4px; white-space: normal; }
.chk > span { display: inline-flex; align-items: center; gap: 5px; }
.chk .ok svg { color: var(--si-ok); }
.chk .no { color: var(--si-danger-ink); }
.chk .no svg { color: var(--si-danger); }
.chk .skip svg { color: var(--si-trip-idle); }
.chk .run svg, .steps3 .run .si { color: var(--si-brand-2); animation: mp-spin 1s linear infinite; }
@keyframes mp-spin { to { transform: rotate(360deg); } }
.spk { display: flex; flex-direction: column; gap: 3px; width: 180px; }
.spk > span { font: 12px var(--si-font-mono); color: var(--si-ink-3); }
.use { display: flex; align-items: center; gap: 10px; width: 136px; }
.use b { display: block; font: 600 15px var(--si-font-mono); color: var(--si-ink-1); }
.use span { font: var(--si-font-caption); color: var(--si-ink-3); }
.ibtn { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.ibtn:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.x3 { display: grid; grid-template-columns: 1fr 1.2fr 1.2fr; gap: 24px; }
h5 { margin: 0 0 10px; font: var(--si-font-overline); color: var(--si-ink-3); }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; margin: 0; font: var(--si-font-meta); }
.kv dt { color: var(--si-ink-3); }
.kv dd { margin: 0; color: var(--si-ink-1); overflow-wrap: anywhere; }
.bal { margin-top: 12px; }
.bal.warn { color: var(--si-warn-ink); }
.acts { display: flex; gap: 4px; margin-top: 14px; }
.danger { color: var(--si-danger-ink); }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.tile { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 14px 16px; text-align: left; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); color: var(--si-ink-1); font: inherit; cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d2); }
.tile:hover:not(:disabled) { transform: translateY(-2px); box-shadow: var(--si-shadow-2); }
.tile.on { border-color: var(--si-ai); box-shadow: 0 0 0 3px var(--si-ai-soft), var(--si-shadow-2); }
.tile:disabled { opacity: .5; cursor: not-allowed; }
.tile b { font: var(--si-font-label); font-weight: 600; margin-top: 8px; }
.glyph { display: grid; place-items: center; width: 32px; height: 32px; border-radius: var(--si-radius-sm); background: var(--si-ai-soft); color: var(--si-ai-ink); font: 600 15px var(--si-font-mono); }
.tile.cus { border-style: dashed; box-shadow: none; }
.tile.cus .glyph { background: var(--si-hover-bg); color: var(--si-ink-2); }
.connect { margin-top: 12px; padding: 20px 22px; }
.frm { display: flex; flex-direction: column; gap: 18px; }
.fr { display: grid; grid-template-columns: 140px minmax(0, 360px); column-gap: 20px; align-items: start; }
.fr > label { font: var(--si-font-label); color: var(--si-ink-2); line-height: 36px; }
.fx { display: flex; flex-direction: column; gap: 6px; }
.fx em { font-style: normal; font: var(--si-font-caption); color: var(--si-ink-3); }
.ro { display: flex; align-items: center; height: 36px; padding: 0 4px 0 12px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-control); background: var(--si-card-2); color: var(--si-ink-2); font-size: 13px; }
.go { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.steps3 { display: flex; gap: 14px; margin: 0; padding: 0; list-style: none; font: var(--si-font-meta); color: var(--si-ink-4); }
.steps3 li { display: inline-flex; align-items: center; gap: 5px; }
.steps3 li span.si { display: inline-block; width: 9px; height: 9px; margin: 0 3px; border: 1.5px solid var(--si-trip-idle); border-radius: 50%; }
.steps3 .ok { color: var(--si-ink-1); }
.steps3 .ok .si { color: var(--si-ok); }
.steps3 .run { color: var(--si-ink-1); }
.steps3 .no { color: var(--si-danger-ink); }
.skel { padding: 40px; text-align: center; color: var(--si-ink-3); }
@media (max-width: 1360px) { .x3 { grid-template-columns: 1fr 1fr; } .x3 > :last-child { grid-column: 1 / -1; } .spk { width: 150px; } }
</style>
