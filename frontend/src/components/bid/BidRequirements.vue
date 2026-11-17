<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Check, Pencil, Plus, RotateCcw, Sparkles, Trash2 } from '@lucide/vue'
import { ApiError } from '@/api/http'
import {
  bidApi, bidGroupLabel, bidMethodLabel, bidSourceLabel,
  type BidGroup, type BidLoc, type BidMethod, type BidParameters, type BidRequirement, type BidRequirements,
} from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidStatus from './BidStatus.vue'
import BidSentDrawer from './BidSentDrawer.vue'
import BidTenderText from './BidTenderText.vue'

/** 第 1 步右半：关键参数、要求清单与完整性计数（bid.md 2.2）。快捷键：J / K 上下、X 停用、E 改文字。 */
const props = defineProps<{ projectId: string; policy: 'ai' | 'local_only'; disabled: boolean; tenderKey: string }>()
const emit = defineEmits<{ changed: [] }>()

const data = ref<BidRequirements>(), error = ref(''), saving = ref(false)
const params = reactive<BidParameters>({ ceiling: null, currency: 'CNY', validity_days: null, deposit: null, deposit_forms: '', consortium: null, tax_included: null })
const paramsDirty = ref(false), paramError = ref('')
const view = ref<'original' | 'ai'>('original'), focus = ref<BidLoc | null>(null)
const current = ref<string | null>(null), editing = ref<string | null>(null), draft = ref('')
const sentOpen = ref(false)
const adding = ref<{ group: BidGroup; text: string; method: BidMethod; locs: BidLoc[] } | null>(null)
const showMissing = ref<'star' | 'veto' | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined, generation = 0

const groupOrder: BidGroup[] = ['veto', 'substantive', 'qualification', 'pricing', 'validity', 'signing', 'format']
const grouped = computed(() => groupOrder.map(g => ({ group: g, items: (data.value?.items ?? []).filter(i => i.group === g) })).filter(g => g.items.length))
const flat = computed(() => grouped.value.flatMap(g => g.items))
const covered = computed(() => (data.value?.items ?? []).filter(i => i.enabled).flatMap(i => i.locs))
const extracting = computed(() => data.value?.extract_state === 'redacting' || data.value?.extract_state === 'extracting')
const locked = computed(() => props.disabled || saving.value || extracting.value)
const triOptions = [{ label: '未写明', value: 'null' }, { label: '是', value: 'true' }, { label: '否', value: 'false' }]
const viewOptions: { label: string; value: 'original' | 'ai' }[] = [{ label: '原文', value: 'original' }, { label: 'AI 视野', value: 'ai' }]
const tri = (v: boolean | null) => String(v)
const fromTri = (v: string) => (v === 'null' ? null : v === 'true')
// 长金额加千分位，便于核对（只做显示，提交仍是原字符串）
function grouping(v: string | null) {
  const m = /^(\d+)(\.\d+)?$/.exec((v ?? '').trim())
  return m && m[1]!.length > 4 ? m[1]!.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (m[2] ?? '') : ''
}

function accept(d: BidRequirements) {
  data.value = d
  if (!paramsDirty.value) Object.assign(params, d.parameters)
  if (timer) clearTimeout(timer)
  timer = extracting.value ? setTimeout(() => void load(), 2000) : undefined
  if (d.extract_state !== 'awaiting' && view.value === 'ai') view.value = 'original'
}
async function load() {
  const g = generation
  try { const d = await bidApi.requirements(props.projectId); if (g === generation) accept(d) }
  catch (e) { if (g === generation) error.value = e instanceof ApiError ? e.message : '要求清单读取失败' }
}
watch(() => [props.projectId, props.tenderKey], () => { generation++; void load() }, { immediate: true })
onBeforeUnmount(() => { generation++; if (timer) clearTimeout(timer) })

async function act(action: () => Promise<BidRequirements>) {
  if (locked.value) return false
  saving.value = true; error.value = ''; generation++
  try { accept(await action()); emit('changed'); return true }
  catch (e) { error.value = e instanceof ApiError ? e.message : '操作失败，请重试'; return false }
  finally { saving.value = false }
}

async function saveParams() {
  paramError.value = ''
  const before = error.value
  paramsDirty.value = false
  if (!await act(() => bidApi.parameters(props.projectId, { ...params }))) { paramsDirty.value = true; paramError.value = error.value; error.value = before }
}
// 乐观更新：开关与检查方式立即生效，失败时由重新读取回滚
async function patch(item: BidRequirement, body: Partial<Pick<BidRequirement, 'group' | 'text' | 'method' | 'enabled'>>) {
  Object.assign(item, body)
  if (!await act(() => bidApi.patchRequirement(props.projectId, item.id, body))) void load()
}
function startEdit(item: BidRequirement) { if (!locked.value) { editing.value = item.id; draft.value = item.text } }
async function saveEdit(item: BidRequirement) {
  const text = draft.value.trim()
  editing.value = null
  if (text && text !== item.text) await patch(item, { text })
}
async function remove(item: BidRequirement) { await act(() => bidApi.removeRequirement(props.projectId, item.id)) }
// 划选加入时按关键词猜分组（与后端规则一致），用户可改
function guessGroup(text: string): BidGroup {
  if (/否决|废标|无效投标|投标无效|不予受理|拒收/.test(text)) return 'veto'
  if (/★|▲/.test(text)) return 'substantive'
  if (/报价|投标价|限价|价格|总价|单价|含税|币种|控制价/.test(text)) return 'pricing'
  if (/有效期|保证金|保函/.test(text)) return 'validity'
  if (/盖章|签字|签章|公章|授权委托|签名|骑缝/.test(text)) return 'signing'
  if (/资质|资格|营业执照|业绩|许可证|认证|信誉|财务|社保|联合体|注册资本|失信/.test(text)) return 'qualification'
  return 'substantive'
}
function startAdd(loc?: BidLoc, text = '') { adding.value = { group: guessGroup(text), text, method: props.policy === 'ai' ? 'model' : 'manual', locs: loc ? [loc] : [] } }
async function saveAdd() {
  if (!adding.value?.text.trim()) return
  const body = { ...adding.value, text: adding.value.text.trim() }
  if (await act(() => bidApi.addRequirement(props.projectId, body))) adding.value = null
}
async function reextract() {
  try { await ElMessageBox.confirm('按当前招标文件重新抽取：手工加入的要求保留，其余（含模型补充与修改过的行）重建。', '重新抽取要求清单', { confirmButtonText: '重新抽取', cancelButtonText: '取消' }) }
  catch { return }
  await act(() => bidApi.reextract(props.projectId))
}
async function assist(action: 'start' | 'confirm' | 'cancel') {
  if (await act(() => bidApi.assist(props.projectId, action)) && action === 'confirm') view.value = 'original'
}
async function confirmList() { await act(() => bidApi.confirm(props.projectId)) }

function locate(item: BidRequirement) { current.value = item.id; focus.value = item.locs[0] ?? null }
function onKey(event: KeyboardEvent) {
  if (editing.value || adding.value || (event.target as HTMLElement).closest('input, textarea, .el-select')) return
  const list = flat.value, index = list.findIndex(i => i.id === current.value)
  const key = event.key.toLowerCase()
  if (key === 'j' || key === 'k') {
    const next = list[Math.max(0, Math.min(list.length - 1, index + (key === 'j' ? 1 : -1)))]
    if (next) { locate(next); document.getElementById(`req-${next.id}`)?.focus() }
  } else if (key === 'x' && index >= 0) void patch(list[index]!, { enabled: !list[index]!.enabled })
  else if (key === 'e' && index >= 0) startEdit(list[index]!)
  else return
  event.preventDefault()
}
const countLine = (kind: 'star' | 'veto') => {
  const c = data.value?.counts[kind]
  if (!c) return ''
  const name = kind === 'star' ? '原文 ★' : '含否决字样的句子'
  if (!c.total) return `${name}：未发现`
  return c.covered === c.total ? `${name}共 ${c.total} 处，已全部进入清单` : `${name}共 ${c.total} 处，已进入 ${c.covered}，未进入 ${c.total - c.covered}`
}
const remainingText = computed(() => {
  const r = data.value?.receipt?.remaining
  if (!r) return ''
  const parts = [r.monthly !== null ? `本月剩余 ${r.monthly} 次` : '', r.daily !== null ? `今日个人剩余 ${r.daily} 次` : ''].filter(Boolean)
  return parts.length ? parts.join('，') : '不限次数'
})
const notEnough = computed(() => {
  const r = data.value?.receipt
  return !!r && [r.remaining.monthly, r.remaining.daily].some(v => v !== null && v < r.calls)
})
</script>

<template>
  <div class="reqs" @keydown="onKey">
    <el-alert v-if="error" class="alert" :title="error" type="error" show-icon @close="error = ''" />
    <template v-if="data">
      <!-- 关键参数 -->
      <section class="params" aria-labelledby="params-title">
        <div class="section-head">
          <h2 id="params-title" class="si-overline">关键参数</h2>
          <span class="hint">{{ data.parameters_confirmed ? '已核对' : '系统预填，请核对改正' }}</span>
        </div>
        <div class="param-grid" @input="paramsDirty = true">
          <label><span>最高限价<i v-if="grouping(params.ceiling)" class="grouped num">{{ grouping(params.ceiling) }}</i></span><el-input v-model="params.ceiling" :disabled="locked" placeholder="未写明" class="num"><template #append><el-select v-model="params.currency" :disabled="locked" style="width: 96px" aria-label="币种" @change="paramsDirty = true"><el-option label="人民币" value="CNY" /><el-option label="美元" value="USD" /><el-option label="欧元" value="EUR" /><el-option label="港币" value="HKD" /></el-select></template></el-input></label>
          <label><span>投标有效期（天）</span><el-input-number v-model="params.validity_days" :disabled="locked" :min="1" :max="3650" :controls="false" placeholder="未写明" @change="paramsDirty = true" /></label>
          <label><span>保证金（元）<i v-if="grouping(params.deposit)" class="grouped num">{{ grouping(params.deposit) }}</i></span><el-input v-model="params.deposit" :disabled="locked" placeholder="未写明" class="num" /></label>
          <label><span>保证金形式</span><el-input v-model="params.deposit_forms" :disabled="locked" maxlength="100" placeholder="如 银行保函、电汇" /></label>
          <div class="field"><span>接受联合体</span><SiSegmented :model-value="tri(params.consortium)" :options="triOptions" size="small" ariaLabel="是否接受联合体" @update:model-value="(v: string) => { params.consortium = fromTri(v); paramsDirty = true }" /></div>
          <div class="field"><span>报价含税</span><SiSegmented :model-value="tri(params.tax_included)" :options="triOptions" size="small" ariaLabel="报价是否含税" @update:model-value="(v: string) => { params.tax_included = fromTri(v); paramsDirty = true }" /></div>
        </div>
        <p v-if="paramError" class="error" role="alert">{{ paramError }}</p>
        <div v-if="paramsDirty" class="param-actions"><el-button size="small" @click="paramsDirty = false; Object.assign(params, data.parameters)">还原</el-button><el-button size="small" type="primary" :loading="saving" @click="saveParams">保存参数</el-button></div>
      </section>

      <!-- 要求清单：左原文，右清单 -->
      <div class="section-head list-head">
        <h2 class="si-overline">要求清单 <span class="num">{{ data.items.filter(i => i.enabled).length }} / {{ data.items.length }}</span></h2>
        <div class="list-tools">
          <SiSegmented v-if="data.extract_state === 'awaiting'" v-model="view" :options="viewOptions" size="small" ariaLabel="画布视图" />
          <el-button size="small" text :disabled="locked" @click="reextract"><RotateCcw :size="14" :stroke-width="1.75" />重新抽取</el-button>
          <el-button size="small" :disabled="locked" @click="startAdd()"><Plus :size="14" :stroke-width="1.75" />添加要求</el-button>
        </div>
      </div>
      <p v-if="data.tender_changed" class="notice">招标文件有变化（加入或移除了文件）。需要时点“重新抽取”，手工加入的要求会保留。</p>

      <div class="split">
        <BidTenderText class="left" :project-id="projectId" :view="view" :reload-key="`${tenderKey}:${data.extract_state}`" :focus="focus" :covered="covered" @add="(loc, text) => startAdd(loc, text)" />
        <div class="right">
          <!-- 完整性计数器：用户只处理差额 -->
          <ul class="counter" aria-label="完整性计数">
            <li v-for="kind in (['star', 'veto'] as const)" :key="kind">
              <Check v-if="data.counts[kind].covered === data.counts[kind].total" :size="14" :stroke-width="2" class="ok" aria-hidden="true" />
              <span v-else class="dot warn" aria-hidden="true" />
              <span>{{ countLine(kind) }}</span>
              <button v-if="data.counts[kind].missing.length" type="button" class="link" @click="showMissing = showMissing === kind ? null : kind">{{ showMissing === kind ? '收起' : '查看' }}</button>
            </li>
          </ul>
          <ul v-if="showMissing" class="missing">
            <li v-for="m in data.counts[showMissing].missing" :key="`${m.loc}:${m.start}`">
              <button type="button" class="quote" @click="focus = m">{{ m.text }}</button>
              <el-button size="small" text :disabled="locked" @click="startAdd(m, m.text)">加入</el-button>
            </li>
          </ul>

          <!-- 模型补充（只做抽取，不做判断） -->
          <div v-if="policy === 'ai'" class="assist">
            <template v-if="!data.extract_state || data.extract_state === 'failed'">
              <Sparkles :size="16" :stroke-width="1.75" aria-hidden="true" />
              <p>{{ data.extract_state === 'failed' ? `模型补充未完成：${data.extract_error}` : '可让模型在脱敏后的招标文件中归类、补漏（只抽取，不判断）。' }}</p>
              <el-button size="small" :disabled="locked" @click="assist('start')">{{ data.extract_state === 'failed' ? '重新开始' : '让模型补充' }}</el-button>
            </template>
            <template v-else-if="data.extract_state === 'redacting'"><BidStatus tone="run" label="正在本机脱敏招标文件…" /></template>
            <template v-else-if="data.extract_state === 'awaiting' && data.receipt">
              <p>将发送脱敏后的招标文件约 <b class="num">{{ data.receipt.chars }}</b> 字，已在本机保护 <b class="num">{{ data.receipt.protected }}</b> 项；本次 <b class="num">{{ data.receipt.calls }}</b> 次调用（{{ remainingText }}）。不发送：原件、投标文件、文件名。可切到“AI 视野”核对。</p>
              <div class="assist-actions">
                <el-button size="small" text :disabled="locked" @click="assist('cancel')">不发送</el-button>
                <el-button size="small" type="primary" :disabled="locked || notEnough" @click="assist('confirm')">确认并发送</el-button>
              </div>
              <p v-if="notEnough" class="error">剩余调用次数不足，请联系管理员。</p>
            </template>
            <template v-else-if="data.extract_state === 'extracting'"><BidStatus tone="run" label="模型补充中，结果会标为“模型提取 · 请核对”" /></template>
            <template v-else-if="data.extract_state === 'done'"><BidStatus tone="ok" :label="`模型补充已完成，新增 ${data.items.filter(i => i.source === 'model').length} 条，请核对`" /><el-button size="small" text :disabled="locked" @click="assist('cancel')">关闭</el-button></template>
            <el-button v-if="data.extract_state === 'done' || data.extract_state === 'failed'" text size="small" @click="sentOpen = true">查看实际发送内容</el-button>
          </div>

          <BidSentDrawer v-model="sentOpen" :project-id="projectId" />
          <div v-if="adding" class="add-form">
            <el-input v-model="adding.text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="2000" placeholder="要求文字" aria-label="新要求文字" autofocus />
            <div class="add-row">
              <el-select v-model="adding.group" size="small" aria-label="分组" style="width: 150px"><el-option v-for="g in groupOrder" :key="g" :label="bidGroupLabel[g]" :value="g" /></el-select>
              <el-select v-model="adding.method" size="small" aria-label="检查方式" style="width: 110px"><el-option v-for="(label, m) in bidMethodLabel" :key="m" :label="label" :value="m" :disabled="m === 'model' && policy === 'local_only'" /></el-select>
              <span class="hint">{{ adding.locs.length ? '出处：所选原文' : '无出处' }}</span>
              <el-button size="small" text @click="adding = null">取消</el-button>
              <el-button size="small" type="primary" :disabled="!adding.text.trim() || locked" @click="saveAdd">加入</el-button>
            </div>
          </div>

          <section v-for="g in grouped" :key="g.group" class="group">
            <h3>{{ bidGroupLabel[g.group] }}<span class="num">{{ g.items.filter(i => i.enabled).length }}</span></h3>
            <ul>
              <li v-for="item in g.items" :id="`req-${item.id}`" :key="item.id" tabindex="0" :class="{ off: !item.enabled, current: current === item.id }" @click="locate(item)" @focus="current = item.id">
                <el-switch :model-value="item.enabled" size="small" :disabled="locked" :aria-label="item.enabled ? '停用' : '启用'" @click.stop @update:model-value="(v: string | number | boolean) => patch(item, { enabled: !!v })" />
                <div class="body">
                  <el-input v-if="editing === item.id" v-model="draft" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" maxlength="2000" autofocus @click.stop @keydown.enter.exact.prevent="saveEdit(item)" @keydown.esc="editing = null" @blur="saveEdit(item)" />
                  <p v-else class="text"><span class="num">{{ item.seq }}.</span> {{ item.text }}</p>
                  <p class="meta">
                    <span :class="['source', item.source]">{{ bidSourceLabel[item.source] }}</span>
                    <span v-if="item.file" class="file" :title="item.file">{{ item.file }}</span>
                    <el-select :model-value="item.method" size="small" class="method" :disabled="locked" aria-label="检查方式" @click.stop @update:model-value="(v: BidMethod) => patch(item, { method: v })">
                      <el-option v-for="(label, m) in bidMethodLabel" :key="m" :label="label" :value="m" :disabled="m === 'model' && policy === 'local_only'" />
                    </el-select>
                  </p>
                </div>
                <div class="row-actions">
                  <button type="button" class="icon" :disabled="locked" aria-label="修改文字" title="修改文字（E）" @click.stop="startEdit(item)"><Pencil :size="14" :stroke-width="1.75" /></button>
                  <button type="button" class="icon" :disabled="locked" aria-label="删除" title="删除" @click.stop="remove(item)"><Trash2 :size="14" :stroke-width="1.75" /></button>
                </div>
              </li>
            </ul>
          </section>
          <p class="hint keys">J / K 上下 · X 停用 · E 改文字 · 在左侧原文中划选可加入清单</p>
        </div>
      </div>

      <footer class="foot">
        <BidStatus v-if="data.stage !== 'preparing'" tone="ok" label="清单已确认，项目待开标；之后仍可修改" />
        <span v-else class="hint">确认后项目进入“待开标”。</span>
        <el-button type="primary" :disabled="locked || paramsDirty" @click="confirmList">{{ data.stage === 'preparing' ? '确认参数与清单' : '重新确认' }}</el-button>
      </footer>
    </template>
    <p v-else-if="!error" class="hint" role="status">正在读取要求清单…</p>
  </div>
</template>

<style scoped>
.alert { margin-bottom: 16px; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding-bottom: 10px; margin-bottom: 12px; border-bottom: 1px solid var(--si-hair-strong); }
.section-head .si-overline { margin: 0; }
.params { margin-bottom: 32px; }
.param-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px 24px; }
.param-grid label, .param-grid .field { display: flex; flex-direction: column; gap: 6px; font: var(--si-font-meta); color: var(--si-ink-3); }
.param-grid .el-input-number { width: 100%; }
.grouped { margin-left: 8px; font-style: normal; color: var(--si-ink-2); }
.param-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.list-head .si-overline .num { margin-left: 6px; color: var(--si-ink-4); }
.list-tools { display: flex; align-items: center; gap: 8px; }
.list-tools .el-button svg { margin-right: 4px; }
.notice { margin: 0 0 12px; font: var(--si-font-meta); color: var(--si-warn-ink); }
.split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 32px; height: min(72vh, 820px); }
.left { min-height: 0; border-right: 1px solid var(--si-hair); }
.right { min-height: 0; overflow: auto; padding-right: 4px; }
.counter { margin: 0 0 8px; padding: 0; list-style: none; }
.counter li { display: flex; align-items: center; gap: 8px; padding: 4px 0; font: var(--si-font-body); color: var(--si-ink-1); }
.ok { color: var(--si-ok-ink); }
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.dot.warn { background: var(--si-warn); }
.link { border: 0; background: none; padding: 0; color: var(--si-brand); font: var(--si-font-meta); cursor: pointer; }
.missing { margin: 0 0 12px 15px; padding: 0; list-style: none; }
.missing li { display: flex; align-items: flex-start; gap: 8px; padding: 4px 0; }
.quote { flex: 1; min-width: 0; text-align: left; border: 0; background: none; padding: 0; font: var(--si-font-meta); color: var(--si-ink-2); cursor: pointer; }
.assist { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; margin: 8px 0 16px; padding: 10px 0; border-top: 1px solid var(--si-hair); border-bottom: 1px solid var(--si-hair); font: var(--si-font-meta); color: var(--si-ink-2); }
.assist p { flex: 1; min-width: 200px; margin: 0; }
.assist-actions { display: flex; gap: 6px; margin-left: auto; }
.add-form { margin: 0 0 16px; padding: 12px 0; border-bottom: 1px solid var(--si-hair); }
.add-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.add-row .hint { margin-right: auto; }
.group { margin-bottom: 18px; }
.group h3 { display: flex; align-items: baseline; gap: 8px; margin: 0 0 4px; font: var(--si-font-label); color: var(--si-ink-2); }
.group h3 .num { color: var(--si-ink-4); font-weight: 400; }
.group ul { margin: 0; padding: 0; list-style: none; }
.group li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 8px 6px; border-bottom: 1px solid var(--si-hair-faint); border-radius: 4px; cursor: pointer; outline: none; }
.group li:hover { background: var(--si-hover-bg); }
.group li.current { background: var(--si-select-bg); }
.group li.off .text { color: var(--si-ink-4); text-decoration: line-through; }
.text { margin: 0; font: var(--si-font-body); color: var(--si-ink-1); overflow-wrap: anywhere; }
.meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.source.model { color: var(--si-warn-ink); }
.file { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.method { width: 96px; }
.row-actions { display: flex; gap: 2px; opacity: 0; }
.group li:hover .row-actions, .group li.current .row-actions { opacity: 1; }
.icon { display: grid; place-items: center; width: 26px; height: 26px; border: 0; border-radius: 4px; background: none; color: var(--si-ink-3); cursor: pointer; }
.icon:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.keys { margin: 8px 0 0; }
.foot { display: flex; align-items: center; justify-content: flex-end; gap: 16px; margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--si-hair); }
.error { margin: 8px 0 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
@media (max-width: 1100px) { .split { grid-template-columns: minmax(0, 1fr); height: auto; } .left { height: 50vh; border-right: 0; } .param-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
