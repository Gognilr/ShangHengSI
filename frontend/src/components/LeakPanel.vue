<script setup lang="ts">
/** 出口复检拦截清单（V31-U5F-02）：逐条列出被拦下的位置，可定位、替换为代号或确认可以发送。
 *  预览是本人原文，只在本页显示；确认页与智能助手、会议纪要的确认页共用。 */
import { computed, ref } from 'vue'
import { ApiError } from '@/api/http'
import { REDACTION_ERROR_MESSAGES, redactionApi, type Leak, type RedactionInfo } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import { MANUAL_CATEGORY_OPTIONS } from '@/utils/labels'

const props = defineProps<{ taskId: string; files: TaskFile[]; locked?: boolean; summary?: string }>()
const emit = defineEmits<{
  (e: 'locate', loc: string, text: string): void
  /** 替换为代号成功：父组件按返回值刷新脱敏结果 */
  (e: 'changed', info: RedactionInfo): void
}>()

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
    items.value = list.items
    other.value = list.other
    loaded.value = true
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请重试'
  } finally { loading.value = false }
}
defineExpose({ reload })

function where(loc: string): string {
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
    await reload()
  } catch (e) { fail(e) } finally { busy.value = false }
}
async function keep(x: Leak) {
  if (!x.keepable || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const list = await redactionApi.keepLeak(props.taskId, { loc: x.loc, char_start: x.char_start, char_end: x.char_end })
    items.value = list.items
    other.value = list.other
  } catch (e) { fail(e) } finally { busy.value = false }
}
</script>

<template>
  <section v-loading="loading" class="leaks" aria-live="polite">
    <h3>出口复检拦下了 {{ loaded ? items.length + other : '' }} 处，未发送</h3>
    <p v-if="summary" class="sum">{{ summary }}</p>
    <p v-if="done" class="ok">已全部处理，可以重新确认发送。</p>
    <ul v-else class="list">
      <li v-for="x in items" :key="key(x)" class="item">
        <div class="head">
          <span class="label">{{ x.label }}</span>
          <button type="button" class="where" :title="'在原文中定位'" @click="emit('locate', x.loc, x.text)">{{ where(x.loc) || '定位' }}</button>
        </div>
        <p class="preview"><span class="ctx">{{ x.before }}</span><mark>{{ x.text }}</mark><span class="ctx">{{ x.after }}</span></p>
        <div class="ops">
          <el-button v-if="x.replace_category" size="small" type="primary" plain :disabled="locked || busy" @click="replace(x)">
            替换为代号（{{ MANUAL_CATEGORY_OPTIONS[x.replace_category] }}）
          </el-button>
          <el-button v-if="x.keepable" size="small" :disabled="locked || busy" @click="keep(x)">不敏感，可以发送</el-button>
          <span v-if="!x.replace_category && !x.keepable" class="hint">请在原文中删改这里后重新上传</span>
        </div>
      </li>
      <li v-if="other" class="item other">另有 {{ other }} 处在主体说明、报价汇总等系统生成的内容中，请检查签约方的国家、类型后重试。</li>
    </ul>
    <p v-if="error" class="err">{{ error }}</p>
    <p class="note">预览只在你的浏览器中显示，不写日志、不发送。</p>
  </section>
</template>

<style scoped>
.leaks { padding: 12px; border: 1px solid var(--el-color-warning-light-5); border-radius: 8px; background: var(--el-color-warning-light-9); }
h3 { margin: 0 0 4px; font: var(--si-font-h4); color: var(--el-color-warning-dark-2); }
.sum, .note, .hint { margin: 0; font: var(--si-font-caption); color: var(--el-text-color-secondary); }
.ok { margin: 4px 0; color: var(--el-color-success); font: var(--si-font-body); }
.list { margin: 8px 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
.item { padding: 8px; border-radius: 6px; background: var(--el-bg-color); }
.item.other { font: var(--si-font-caption); color: var(--el-text-color-regular); }
.head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.label { font: var(--si-font-body); font-weight: 600; }
.where { padding: 0; border: 0; background: none; color: var(--el-color-primary); font: var(--si-font-caption); cursor: pointer; text-align: right; }
.where:hover { text-decoration: underline; }
.where:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.preview { margin: 6px 0; font: var(--si-font-body); word-break: break-all; }
.ctx { color: var(--el-text-color-secondary); }
mark { padding: 0 2px; border-radius: 3px; background: var(--el-color-warning-light-7); color: var(--el-text-color-primary); }
.ops { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.ops .el-button + .el-button { margin-left: 0; }
.err { margin: 4px 0; color: var(--el-color-danger); font: var(--si-font-caption); }
</style>
