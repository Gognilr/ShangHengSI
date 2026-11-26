<script setup lang="ts">
/** 发送前复检拦截清单（V31-U5F-02；二版 6.7 改用 SiNotice 的拦截语气）：琥珀浅底卡，逐处列出命中内容，每处都有一键处理，处理完直接重新发送。
 *  预览是本人原文，只在本页显示；确认页与智能助手、会议纪要的确认页共用。 */
import { computed, ref } from 'vue'
import { Check, ShieldAlert } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { REDACTION_ERROR_MESSAGES, redactionApi, type Leak, type LeakList, type RedactionInfo } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'

const props = defineProps<{ taskId: string; files: TaskFile[]; locked?: boolean; summary?: string }>()
const emit = defineEmits<{
  (e: 'locate', loc: string, text: string): void
  /** 替换为代号成功：父组件按返回值刷新脱敏结果 */
  (e: 'changed', info: RedactionInfo): void
  /** 还没处理的处数（加载成功后才报），父组件据此锁定确认按钮 */
  (e: 'pending', n: number): void
  /** 命中在由签约方生成的主体说明里：父组件定位到签约方卡片 */
  (e: 'parties'): void
}>()
/** 本次已处理的几处：留在清单里显示“已处理”，让人看到每处都有了结果 */
const handled = ref<{ key: string; text: string; how: string }[]>([])

const items = ref<Leak[]>([])
const other = ref(0)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const loaded = ref(false)

async function reload() {
  loading.value = true
  error.value = ''
  try {
    const list = await redactionApi.leaks(props.taskId)
    apply(list)
    loaded.value = true
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请重试'
  } finally { loading.value = false }
}
function apply(list: LeakList) {
  items.value = list.items
  other.value = list.other
  emit('pending', list.items.length + list.other)
}
defineExpose({ reload })

function where(x: Leak): string {
  const place = x.loc === 'OUT' ? '' : at(x.loc)
  return x.section ? [x.section === '正文' ? '正文与相邻段落' : x.section, place].filter(Boolean).join(' · ') : place
}
/** 不能就地处理时告诉用户去哪里改 */
function fixHint(x: Leak): string {
  if (x.section === '主体说明') return '这段由签约方的名称、国家、类型生成：请在下方“签约方与我方”中检查并修改'
  if (x.section === '报价汇总') return '这段由原文的报价表生成：请在原文表格中删改后重新上传'
  return '请在原文中删改这里后重新上传'
}
function at(loc: string): string {
  if (loc === 'NOTE') return '审核要求'
  const file = props.files.find(f => loc.startsWith(`F${f.seq}-`))
  const rest = loc.replace(/^F\d+-/, '')
  const m = /^P(\d+)$/.exec(rest) ?? /^PG(\d+)/.exec(rest) ?? /^T(\d+)-R(\d+)/.exec(rest)
  const part = !m ? '' : rest.startsWith('PG') ? `第 ${m[1]} 页` : rest.startsWith('T') ? `表 ${m[1]} 第 ${m[2]} 行` : `第 ${m[1]} 段`
  return [file?.original_name, part].filter(Boolean).join(' · ')
}

const key = (x: Leak) => `${x.loc}:${x.char_start}:${x.char_end}:${x.category}`
const done = computed(() => loaded.value && !items.value.length && !other.value)

function fail(e: unknown) {
  error.value = e instanceof ApiError ? REDACTION_ERROR_MESSAGES[e.code] ?? e.message : '操作失败，请重试'
}
async function replace(x: Leak) {
  if (!x.replace_category || busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit('changed', await redactionApi.manual(props.taskId, { loc: x.loc, char_start: x.char_start, char_end: x.char_end, category: x.replace_category }))
    handled.value = [...handled.value, { key: key(x), text: x.text, how: '已替换为代号' }]
    await reload()
  } catch (e) { fail(e) } finally { busy.value = false }
}
async function keep(x: Leak) {
  if (!x.keepable || busy.value) return
  busy.value = true
  error.value = ''
  try {
    apply(await redactionApi.keepLeak(props.taskId, { loc: x.loc, char_start: x.char_start, char_end: x.char_end }))
    handled.value = [...handled.value, { key: key(x), text: x.text, how: '确认可以发送' }]
  } catch (e) { fail(e) } finally { busy.value = false }
}
</script>

<template>
  <section v-loading="loading" class="block-card" aria-live="polite">
    <div class="bc-h"><span class="bc-ic"><ShieldAlert :size="16" :stroke-width="2" /></span><b>{{ done ? '拦下的内容都处理好了' : `发送前复检拦下 ${loaded ? items.length + other : ''} 处未遮住的内容` }}</b></div>
    <p class="bc-p">{{ done ? '可以重新发送。' : '这些内容像真实名称或联系方式，但没有被遮住，所以这次没有发送。逐处处理后可以直接重新发送。' }}</p>
    <p v-if="summary && !done" class="bc-sum">{{ summary }}</p>
    <ul class="hits">
      <li v-for="x in items" :key="key(x)" class="hit">
        <div class="hit-main">
          <b :title="x.text">{{ x.text }}</b>
          <button v-if="x.loc !== 'OUT'" type="button" class="where" title="在原文中定位" @click="emit('locate', x.loc, x.text)">{{ where(x) || '定位' }} · {{ x.label }}</button>
          <span v-else class="where-static">{{ where(x) }} · {{ x.label }}</span>
          <span class="ctx">{{ x.before }}<mark>{{ x.text }}</mark>{{ x.after }}</span>
        </div>
        <div class="ops">
          <el-button v-if="x.replace_category" size="small" :disabled="locked || busy" @click="replace(x)">替换为代号</el-button>
          <el-button v-if="x.keepable" size="small" text :disabled="locked || busy" @click="keep(x)">不敏感，可以发送</el-button>
          <el-button v-if="!x.replace_category && x.section === '主体说明'" size="small" @click="emit('parties')">去改签约方</el-button>
          <span v-else-if="!x.replace_category && !x.keepable" class="hint">{{ fixHint(x) }}</span>
        </div>
      </li>
      <li v-for="h in handled" :key="`done:${h.key}`" class="hit done">
        <div class="hit-main"><b>{{ h.text }}</b><span class="where-static">{{ h.how }}</span></div>
        <span class="ok-badge"><Check :size="13" :stroke-width="2" />已处理</span>
      </li>
      <li v-if="other" class="hit other">另有 {{ other }} 处在系统生成的内容中，请点“查看将发送的全文”查找。</li>
    </ul>
    <p v-if="error" class="err">{{ error }}</p>
    <p class="note">预览只在你的浏览器中显示，不写日志、不发送。</p>
  </section>
</template>

<style scoped>
.block-card { padding: 14px 16px; border: 1px solid var(--si-warn-line); border-radius: var(--si-radius-card); background: var(--si-warn-soft); animation: bc-in var(--si-d3) var(--si-ease-out) both; }
@keyframes bc-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: none; } }
.bc-h { display: flex; align-items: center; gap: 10px; color: var(--si-ink-1); font: var(--si-font-h4); }
.bc-ic { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border-radius: var(--si-radius-sm); background: var(--si-surface-1); color: var(--si-warn-ink); }
.bc-p { margin: 6px 0 0; color: var(--si-ink-2); font: var(--si-font-body); }
.bc-sum, .note, .hint { margin: 4px 0 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.hits { margin: 10px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.hit { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--si-radius-control); background: var(--si-surface-1); }
.hit.done { opacity: .78; }
.hit.other { color: var(--si-ink-2); font: var(--si-font-caption); }
.hit-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.hit-main b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.where { align-self: flex-start; max-width: 100%; padding: 0; overflow: hidden; border: 0; background: none; color: var(--si-ink-3); font: var(--si-font-caption); text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.where:hover { color: var(--si-blue-ink); text-decoration: underline; }
.where:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.where-static { color: var(--si-ink-3); font: var(--si-font-caption); }
.ctx { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
mark { padding: 0 2px; border-radius: 3px; background: var(--si-warn-soft); color: var(--si-warn-ink); }
.ops { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.ops :deep(.el-button + .el-button) { margin-left: 0; }
.ops .hint { max-width: 140px; text-align: right; }
.ok-badge { flex: none; display: inline-flex; align-items: center; gap: 4px; height: 22px; padding: 0 8px; border-radius: var(--si-radius-badge); background: var(--si-ok-soft); color: var(--si-ok-ink); font: var(--si-font-badge); }
.err { margin: 6px 0 0; color: var(--si-danger-ink); font: var(--si-font-caption); }
</style>
