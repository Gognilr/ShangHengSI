<script setup lang="ts">
import SiBadge from '@/components/ui/SiBadge.vue'
import { ElMessage, type InputInstance } from 'element-plus'
import { BookOpen, Check, Copy, MapPin, MessageSquare, Undo2 } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { sharedContentApi } from '@/api/sharedContent'
import { ApiError } from '@/api/http'
import { runsApi, type Finding, type HandlingStatus } from '@/api/runs'
import MarkedText from '@/components/result/MarkedText.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { useAuthStore } from '@/stores/auth'
import { CARRY_LABELS, CITE_TAG, categoryLabel, citeStatusLabel, severityLabel } from '@/utils/labels'

/**
 * 问题卡片（V31-U3 3.5，澄明 8.4 ③）：等级、位置、序号 → 一句话结论 → 为什么 → 依据 → 建议条文（可复制）→ 采纳 / 忽略 / 追问。
 * 收起时只显示结论和两行说明，选中时展开全文；模型文字一律经 MarkedText 纯文本渲染。
 */
const props = defineProps<{ finding: Finding; no: string; selected?: boolean; askEnabled?: boolean; discussEnabled?: boolean }>()
const emit = defineEmits<{
  (e: 'updated', f: Finding): void
  (e: 'select', f: Finding): void
  (e: 'ask', prefill: string): void
  (e: 'discuss', f: Finding): void
  /** 点了“采纳”或“忽略”并已保存：问题栏收起本条、展开下一条待处理（V33-C-02） */
  (e: 'handled', f: Finding): void
}>()

const f = computed(() => props.finding)
const auth = useAuthStore()
const sharing = ref(false)

async function changeSharing() {
  if (sharing.value || saving.value) return
  const id = f.value.id
  sharing.value = true
  try {
    const { active } = await sharedContentApi.finding(id, !f.value.shared)
    if (f.value.id === id) emit('updated', { ...f.value, shared: active })
    ElMessage.success(active ? '已沉淀为公司条文' : '已撤回沉淀')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请重试') }
  finally { sharing.value = false }
}
const basisShort = computed(() => {
  const law = f.value.law_refs?.[0]
  if (law) return `《${law.title}》${law.article_no ?? ''}`
  const chars = Array.from(f.value.basis ?? '')
  return chars.length > 20 ? `${chars.slice(0, 20).join('')}…` : chars.join('')
})
const basisLine = computed(() => {
  const laws = (f.value.law_refs ?? []).map(l => `《${l.title}》${l.article_no ?? ''}`)
  return laws.length ? laws.join('；') : basisShort.value
})

const status = ref<HandlingStatus>(props.finding.handling_status)
const note = ref(props.finding.handling_note ?? '')
const saving = ref(false)
const savedMark = ref(false)
let savedTimer: ReturnType<typeof setTimeout> | undefined
const editingNote = ref(false)
const noteInput = ref<InputInstance>()
watch(() => props.finding, (v) => { status.value = v.handling_status; note.value = v.handling_note ?? '' })

async function save(body: { handling_status?: HandlingStatus; handling_note?: string | null; use_discussed?: boolean }) {
  saving.value = true
  try {
    const saved = await runsApi.patchFinding(props.finding.id, body)
    emit('updated', saved)
    if (body.handling_status === 'adopted' || body.handling_status === 'ignored') emit('handled', saved)
    savedMark.value = true // 就地反馈（V321-E-01）：显示在处理状态旁，不再弹全局提示
    clearTimeout(savedTimer)
    savedTimer = setTimeout(() => { savedMark.value = false }, 1600)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    status.value = props.finding.handling_status
    note.value = props.finding.handling_note ?? ''
  } finally { saving.value = false }
}
function setStatus(value: HandlingStatus) {
  if (saving.value || value === props.finding.handling_status) return
  status.value = value
  void save({ handling_status: value })
}
// 讨论修改稿（V32-B-03）：与原审核建议二选一，采纳与批注版取所选文本；原建议不被覆盖
const sourceOptions = [{ value: 'review', label: '原审核建议' }, { value: 'discussed', label: '讨论修改稿' }]
function setSource(value: string) {
  const wanted = value === 'discussed'
  if (saving.value || wanted === !!props.finding.use_discussed) return
  void save({ use_discussed: wanted })
}
async function editNote() { editingNote.value = true; await nextTick(); noteInput.value?.focus() }
function saveNote() {
  if (!editingNote.value) return
  editingNote.value = false
  const text = note.value.trim()
  if (text !== (props.finding.handling_note ?? '')) void save({ handling_note: text })
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 非安全上下文或被浏览器拒绝时的备用做法
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    if (!ok) { ElMessage.error('复制失败，请手动选择文字复制'); return }
  }
  ElMessage.success('已复制，可粘贴到原文中修改')
}
</script>

<template>
  <article class="card" :class="[`severity-${f.severity}`, `h-${status}`, { selected }]">
    <div class="head" role="button" tabindex="0" :aria-expanded="!!selected" @click="emit('select', f)" @keydown.enter.self.prevent="emit('select', f)" @keydown.space.self.prevent="emit('select', f)">
      <div class="top">
        <span v-if="f.carry" class="h-tag c-tag" :class="`c-${f.carry}`">{{ CARRY_LABELS[f.carry] }}</span>
        <span class="sev"><i aria-hidden="true" />{{ severityLabel(f.severity) }}</span>
        <span v-if="f.location" class="loc" :title="f.location"><MapPin :size="12" :stroke-width="2" />{{ f.location }}</span>
        <span v-if="status === 'adopted'" class="h-tag h-tag--adopted"><Check :size="12" :stroke-width="2.5" />已采纳</span>
        <span v-else-if="status === 'ignored'" class="h-tag h-tag--ignored">已忽略</span>
        <span v-if="f.discussed_text" class="h-tag d-tag" :class="{ on: f.use_discussed }" :title="f.use_discussed ? '采纳与批注版使用讨论修改稿' : '有讨论修改稿，当前使用原审核建议'">讨论稿</span>
        <span class="no num">{{ no }}</span>
      </div>
      <b class="title"><MarkedText :text="f.title" :marks="f.marks.title" /><span v-if="f.unresolved > 0" class="unresolved">{{ f.unresolved }} 处未能还原</span></b>
      <p v-if="f.carry_note" class="carry-note">{{ f.carry === 'resolved' ? '已解决的依据' : '说明' }}：{{ f.carry_note }}</p>
      <p v-if="!selected" class="brief"><MarkedText :text="f.problem" :marks="f.marks.problem" /></p>
      <span v-if="!selected" class="cat">{{ categoryLabel(f.category) }}<template v-if="basisShort"> · 依据 {{ basisShort }}</template></span>
    </div>

    <div v-if="selected" class="body">
      <MarkedText class="text" :text="f.problem" :marks="f.marks.problem" />
      <div v-if="f.impact" class="row"><span class="label">对我方的影响</span><MarkedText class="text" :text="f.impact" :marks="f.marks.impact" /></div>
      <blockquote v-if="f.quote_excerpt || f.quote" class="quote">{{ f.quote_excerpt || f.quote }}</blockquote>
      <p v-else class="muted">缺失型问题，没有对应的原文</p>
      <div v-if="basisLine || f.basis" class="basis"><BookOpen :size="14" :stroke-width="1.75" /><span>依据：<MarkedText v-if="f.basis" :text="f.basis" :marks="f.marks.basis" /><template v-else>{{ basisLine }}</template></span></div>
      <el-collapse v-if="f.law_refs?.length" class="laws">
        <el-collapse-item v-for="(law, index) in f.law_refs" :key="index" :name="index" :title="`《${law.title}》${law.article_no ?? ''} 原文`">
          <p class="text">{{ law.text }}</p>
        </el-collapse-item>
      </el-collapse>
      <div v-if="f.suggestion" class="row"><span class="label">修改建议</span><MarkedText class="text" :text="f.suggestion" :marks="f.marks.suggestion" /></div>
      <div v-if="f.suggested_text" class="sugg">
        <div class="sugg-h">
          <span>{{ f.discussed_text ? '原审核建议的条文' : '建议条文' }}</span>
          <span v-if="f.marks.suggested_text?.length" class="copy-warn">含未能还原的写法，复制后请核对</span>
          <div class="sugg-actions">
            <el-button text size="small" @click.stop="copy(f.suggested_text)"><Copy :size="14" :stroke-width="1.75" />复制</el-button>
            <el-button v-if="!f.use_discussed && auth.can('use.review')" text size="small" :disabled="saving"
              :loading="sharing" @click.stop="changeSharing">{{ f.shared ? '撤回沉淀' : '沉淀为公司条文' }}</el-button>
          </div>
        </div>
        <MarkedText class="text" :text="f.suggested_text" :marks="f.marks.suggested_text" />
      </div>
      <div v-if="f.discussed_text" class="sugg discussed" :class="{ chosen: f.use_discussed }">
        <div class="sugg-h">
          <span>讨论修改稿</span>
          <span v-if="f.marks.discussed_text?.length" class="copy-warn">含未能还原的写法，导出时转为批注</span>
          <div class="sugg-actions">
            <el-button text size="small" @click.stop="copy(f.discussed_text)"><Copy :size="14" :stroke-width="1.75" />复制</el-button>
            <el-button v-if="f.use_discussed && auth.can('use.review')" text size="small" :disabled="saving"
              :loading="sharing" @click.stop="changeSharing">{{ f.shared ? '撤回沉淀' : '沉淀为公司条文' }}</el-button>
          </div>
        </div>
        <MarkedText class="text" :text="f.discussed_text" :marks="f.marks.discussed_text" />
      </div>
      <div v-if="f.discussed_text" class="source">
        <span class="label">采纳与批注版使用</span>
        <SiSegmented :model-value="f.use_discussed ? 'discussed' : 'review'" :options="sourceOptions" size="small" ariaLabel="采纳时使用的建议" @update:model-value="setSource(String($event))" />
      </div>
      <el-button v-if="f.shared && !f.suggested_text && !f.discussed_text && auth.can('use.review')" text size="small"
        :loading="sharing" @click.stop="changeSharing">撤回沉淀</el-button>
      <div class="badges">
        <SiBadge :tone="CITE_TAG[f.cite_status] ?? 'info'" plain>{{ citeStatusLabel(f.cite_status) }}</SiBadge>
        <SiBadge v-if="f.law_unverified" tone="warning" plain>引用未经法规库核实</SiBadge>
        <SiBadge v-if="f.source === 'template'" tone="info" plain>范本对照</SiBadge>
        <span class="cat">{{ categoryLabel(f.category) }}</span>
      </div>
      <div class="act">
        <template v-if="status === 'pending'">
          <el-button size="small" :disabled="saving" @click="setStatus('adopted')"><Check :size="14" :stroke-width="2" />采纳</el-button>
          <el-button size="small" text :disabled="saving" @click="setStatus('ignored')">忽略</el-button>
        </template>
        <el-button v-else size="small" text :disabled="saving" @click="setStatus('pending')"><Undo2 :size="14" :stroke-width="1.75" />{{ status === 'adopted' ? '取消采纳' : '取消忽略' }}</el-button>
        <el-button v-if="askEnabled" size="small" text @click="emit('ask', `关于问题 ${no}「${f.title}」：`)"><MessageSquare :size="14" :stroke-width="1.75" />追问</el-button>
        <el-button v-else-if="discussEnabled" size="small" text @click="emit('discuss', f)"><MessageSquare :size="14" :stroke-width="1.75" />追问</el-button>
        <span v-if="saving || savedMark" class="saved-mark" role="status">{{ saving ? '保存中…' : '已保存' }}</span>
        <el-button v-if="!editingNote" class="note-btn" link size="small" :disabled="saving" @click="editNote">{{ f.handling_note ? '改备注' : '加备注' }}</el-button>
      </div>
      <el-input v-if="editingNote" ref="noteInput" v-model="note" size="small" maxlength="500" placeholder="备注（选填）" :disabled="saving" @blur="saveNote" @keyup.enter="saveNote" />
      <p v-else-if="f.handling_note" class="note">备注：{{ f.handling_note }}</p>
    </div>
  </article>
</template>

<style scoped>
/* 问题卡片（澄明 8.4 ③）：白底卡片，等级在左缘一条色线；选中时衡青边与光圈 */
.card { --sev: var(--si-info); --sev-soft: var(--si-info-soft); --sev-ink: var(--si-info-ink); position: relative; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: inset 3px 0 0 var(--sev); transition: border-color var(--si-d2) var(--si-ease-out), box-shadow var(--si-d2) var(--si-ease-out), transform var(--si-d2) var(--si-ease-out); }
.severity-high { --sev: var(--si-danger); --sev-soft: var(--si-danger-soft); --sev-ink: var(--si-danger-ink); }
.severity-medium { --sev: var(--si-warn); --sev-soft: var(--si-warn-soft); --sev-ink: var(--si-warn-ink); }
.card:hover { border-color: var(--si-hair-strong); box-shadow: inset 3px 0 0 var(--sev), var(--si-shadow-2); }
.card.selected { border-color: var(--si-brand-2); box-shadow: inset 3px 0 0 var(--sev), 0 0 0 3px var(--si-ring); }
.card.h-adopted:not(.selected) .head { opacity: .72; }
.card.h-ignored:not(.selected) .head { opacity: .5; }
.head { display: flex; flex-direction: column; gap: 6px; padding: 12px 14px 12px 16px; cursor: pointer; border-radius: var(--si-radius-card); }
.head:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.sev, .loc, .h-tag { display: inline-flex; flex: none; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: var(--si-radius-badge); font: var(--si-font-badge); }
.sev { background: var(--sev-soft); color: var(--sev-ink); }
.sev i { width: 6px; height: 6px; border-radius: 50%; background: var(--sev); }
.loc { flex: 0 1 auto; min-width: 0; max-width: 200px; border: 1px solid var(--si-hair); color: var(--si-ink-3); }
.loc svg { flex: none; }
.loc { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.h-tag--adopted { background: var(--si-ok-soft); color: var(--si-ok-ink); }
.h-tag--ignored { background: var(--si-surface-2); color: var(--si-ink-3); }
/* 增量轮的本轮状态（V35-D-03）：“仍存在 · 条款未改动”用次要文字色，与模型判断的区分开 */
.c-new { background: var(--si-blue-tint); color: var(--si-blue-ink); }
.c-persists { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.c-unchanged { background: var(--si-surface-2); color: var(--si-ink-3); }
.c-resolved { background: var(--si-ok-soft); color: var(--si-ok-ink); }
.carry-note { margin: 4px 0 0; color: var(--si-ink-3); font: var(--si-font-caption); overflow-wrap: anywhere; }
.d-tag { background: var(--si-surface-2); color: var(--si-ink-2); }
.d-tag.on { background: var(--si-select-bg); color: var(--si-ink-1); }
.no { margin-left: auto; color: var(--si-ink-4); font: var(--si-font-code-sm); }
.title { color: var(--si-ink-1); font: var(--si-font-h3); font-size: calc(16px * var(--si-fs)); line-height: 1.45; overflow-wrap: anywhere; }
.unresolved { margin-left: 6px; font: var(--si-font-label-sm); color: var(--si-warn-ink); }
.brief { margin: 0; color: var(--si-ink-2); font: var(--si-font-body); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.cat { color: var(--si-ink-4); font: var(--si-font-caption); }
.body { display: flex; flex-direction: column; gap: 10px; padding: 0 14px 12px 16px; margin-top: -4px; }
.text { color: var(--si-ink-2); font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
.quote { margin: 0; padding: 6px 12px; border-left: 3px solid var(--sev); border-radius: 0 8px 8px 0; background: var(--sev-soft); color: var(--si-ink-2); font: var(--si-font-meta); white-space: pre-wrap; overflow-wrap: anywhere; }
.row { display: flex; flex-direction: column; gap: 2px; }
.label { font: var(--si-font-label-sm); color: var(--si-ink-3); }
.basis { display: flex; align-items: flex-start; gap: 6px; color: var(--si-ink-3); font: var(--si-font-meta); }
.basis svg { flex: none; margin-top: 3px; }
.laws { margin-top: -4px; }
.sugg { padding: 10px 12px; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-md); background: var(--si-card-2); }
.sugg-h { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; margin-bottom: 4px; color: var(--si-ink-3); font: var(--si-font-label-sm); }
.sugg-actions { display: flex; flex-wrap: wrap; margin-left: auto; }
.sugg-actions .el-button + .el-button { margin-left: 0; }
.sugg-h :deep(.el-button > span) { gap: 4px; }
.sugg .text { color: var(--si-ink-1); }
.sugg.discussed.chosen { box-shadow: inset 0 0 0 1px var(--si-brand-2); }
.source { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.copy-warn { font: var(--si-font-caption); color: var(--si-warn-ink); }
.muted { margin: 0; color: var(--si-ink-3); font: var(--si-font-meta); }
.badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
/* 处理按钮固定在卡片可见区的底边（V321-E-05）：长卡片不用滚到最底下才能采纳或忽略 */
.act { position: sticky; bottom: 0; z-index: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 0 2px; border-top: 1px solid var(--si-hair-faint); background: var(--si-surface-1); }
.act :deep(.el-button + .el-button) { margin-left: 0; }
.act :deep(.el-button > span) { gap: 4px; }
.note-btn { margin-left: auto !important; }
.saved-mark { font: var(--si-font-label-sm); color: var(--si-ok-ink); }
.note { margin: 0; color: var(--si-ink-3); font: var(--si-font-meta); overflow-wrap: anywhere; }
</style>
