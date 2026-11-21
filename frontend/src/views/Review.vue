<script setup lang="ts">
// 文件审核首页（V34-A-03，澄明 8.4 ①）：标题与一句说明 + 大号拖放区 + 最近的审核 5 条。
// 拖入、点选或粘贴文件才建会话（不在进入页面时建空会话），上传后进入该会话的审核视图；底层仍是“工作空间就是会话”。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, Ellipsis, FilePlus, FileText, Upload } from '@lucide/vue'
import { assistantApi, type HistoryItem } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { usePasteFiles } from '@/composables/usePasteFiles'
import { useSessionsStore } from '@/stores/sessions'
import { docTypeLabel, stanceLabel, statusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const router = useRouter(), sessions = useSessionsStore()
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png'
const picker = ref<HTMLInputElement>(), dragging = ref(false)
const uploading = ref<number | null>(null), error = ref('')
const recent = ref<HistoryItem[]>([])

async function upload(files: File[]) {
  if (!files.length || uploading.value !== null) return
  if (files.some(f => /\.(mp3|m4a|wav|amr|aac|mp4)$/i.test(f.name))) { error.value = '录音请到“会议纪要”中处理'; return }
  error.value = ''; uploading.value = 0
  try {
    const created = await assistantApi.create()
    await assistantApi.addFiles(created.id, files, (loaded, total) => { uploading.value = Math.round(loaded / total * 100) })
    sessions.markReview(created.id)
    await router.push({ path: `/assistant/${created.id}`, query: { mode: 'review' } })
    void sessions.refresh()
  } catch (e) { error.value = e instanceof ApiError ? e.message : '上传失败，请稍后重试' }
  finally { uploading.value = null }
}
function picked(event: Event) { const input = event.target as HTMLInputElement, list = Array.from(input.files ?? []); input.value = ''; void upload(list) }
function drop(event: DragEvent) { dragging.value = false; dragDepth = 0; void upload(Array.from(event.dataTransfer?.files ?? [])) }
let dragDepth = 0
function dragEnter() { dragDepth += 1; dragging.value = true }
function dragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dragging.value = false }
usePasteFiles(files => void upload(files), () => uploading.value === null)

// 最近的审核：历史记录第一页中发起过审核的会话与旧审核任务，取前 5 条
onMounted(async () => {
  try { recent.value = (await assistantApi.history({ page: 1 })).items.filter(row => row.kind === 'review' || row.run_count > 0).slice(0, 5) }
  catch { /* 列表失败不影响上传 */ }
})
// 每行：名称、一句说明（立场 · 轮次），状态徽标按澄明第十二节着色
type Tone = 'info' | 'mid' | 'hi' | 'brand' | 'neu' | 'ok'
const rows = computed(() => recent.value.map(row => {
  const badges: { text: string; tone: Tone; dot?: boolean }[] = []
  if (row.reviewing) badges.push({ text: '审核中', tone: 'info', dot: true })
  else if (row.blocked) badges.push({ text: '被拦截', tone: 'mid', dot: true })
  else if (row.status === 'awaiting_confirm' || row.status === 'draft') badges.push({ text: '待你确认', tone: 'mid', dot: true })
  else if (row.status === 'parsing') badges.push({ text: '读取中', tone: 'info', dot: true })
  else if (row.status === 'parse_failed') badges.push({ text: '读取失败', tone: 'hi', dot: true })
  else {
    if (row.high_count > 0) badges.push({ text: `高 ${row.high_count}`, tone: 'hi' })
    if (row.latest_run_id && row.pending_count > 0) badges.push({ text: `待处理 ${row.pending_count}`, tone: 'neu' })
    if (!badges.length) badges.push({ text: row.kind === 'session' && row.run_count ? '已审核' : statusLabel(row.status), tone: 'brand' })
  }
  const sub = [row.doc_type ? docTypeLabel(row.doc_type) : '', row.stance ? `${stanceLabel(row.stance)}立场` : '', row.run_count > 1 ? `第 ${row.run_count} 轮` : '', row.file_count > 1 ? `${row.file_count} 份文件` : ''].filter(Boolean).join(' · ')
  return { row, badges, sub }
}))
// “⋯ → 传新一稿”（V35-D-02）：选好文件后传进那次审核，直接认作主文件所在链最新一稿的下一稿，再打开该会话
const draftPicker = ref<HTMLInputElement>()
const draftTarget = ref<HistoryItem | null>(null)
function pickDraft(row: HistoryItem) { draftTarget.value = row; draftPicker.value?.click() }
async function draftPicked(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0], row = draftTarget.value
  input.value = ''
  if (!file || !row || uploading.value !== null) return
  error.value = ''; uploading.value = 0
  try {
    const session = await assistantApi.get(row.id)
    const docs = session.files.filter(f => !f.media)
    let base = docs.find(f => f.is_main) ?? docs[0]
    for (let next = base && docs.find(f => f.prev_file_id === base!.id); next; next = docs.find(f => f.prev_file_id === base!.id)) base = next
    if (!base) throw new Error('这次审核里没有可对照的文件')
    await assistantApi.addFiles(row.id, [file], (loaded, total) => { uploading.value = Math.round(loaded / total * 100) }, [], base.id)
    sessions.markReview(row.id)
    await router.push({ path: `/assistant/${row.id}`, query: { mode: 'review' } })
  } catch (e) { error.value = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '上传失败，请稍后重试' }
  finally { uploading.value = null }
}
function open(row: HistoryItem) {
  if (row.kind === 'session') void router.push({ path: `/assistant/${row.id}`, query: { mode: 'review' } })
  else void router.push(row.status === 'draft' ? { name: 'review-new', query: { task: row.id } } : { name: 'review-result', params: { taskId: row.id } })
}
</script>

<template>
  <div class="page review-home si-stagger">
    <header class="head">
      <h1 class="entry-title">文件审核</h1>
      <p class="entry-lede">合同、报价单、订单、函件都可以。文件在本机读取和遮住名称，你确认后才交给模型初审。</p>
    </header>
    <el-alert v-if="error" type="error" :closable="false" :title="error" class="err" />
    <div class="drop" :class="{ dragging, busy: uploading !== null }" role="button" tabindex="0" :aria-disabled="uploading !== null"
      @click="picker?.click()" @keydown.enter="picker?.click()" @dragenter.prevent="dragEnter" @dragover.prevent @dragleave="dragLeave" @drop.prevent="drop">
      <span class="drop-ic"><Upload :size="26" :stroke-width="1.75" /></span>
      <b class="drop-title">{{ uploading !== null ? `正在上传 ${uploading}%` : dragging ? '松开即可加入' : '拖入要审核的文件，或点击选择' }}</b>
      <span class="hint">Word、PDF、扫描件、图片、Excel、WPS，可一次加入多份 · 也可以按 Ctrl+V 粘贴</span>
      <el-button v-if="uploading === null && !dragging" type="primary" class="pick" @click.stop="picker?.click()"><FilePlus :size="16" :stroke-width="1.75" />选择文件</el-button>
      <span v-if="uploading !== null" class="bar" aria-hidden="true"><i :style="{ width: `${uploading}%` }" /></span>
    </div>
    <input ref="picker" class="file-input" type="file" multiple :accept="DOC" @change="picked">
    <input ref="draftPicker" class="file-input" type="file" :accept="DOC" @change="draftPicked">
    <section v-if="rows.length" class="recent">
      <div class="recent-head"><h2>最近的审核</h2><router-link to="/history" class="all">全部历史<ChevronRight :size="14" :stroke-width="1.75" /></router-link></div>
      <div class="list">
        <a v-for="({ row, badges, sub }, i) in rows" :key="row.id" href="#" class="li" :style="{ '--i': i }" @click.prevent="open(row)">
          <span class="mod-ic"><FileText :size="18" :stroke-width="1.75" /></span>
          <span class="li-main"><b :title="row.title">{{ row.title }}</b><span v-if="sub">{{ sub }}</span></span>
          <span v-for="b in badges" :key="b.text" class="badge" :class="[`b-${b.tone}`, { breath: b.tone === 'info' }]"><i v-if="b.dot" class="d" />{{ b.text }}</span>
          <span class="li-t num">{{ formatDateTime(row.last_activity_at) }}</span>
          <el-dropdown v-if="row.kind === 'session'" trigger="click" @command="() => pickDraft(row)" @click.prevent.stop>
            <button type="button" class="more" :aria-label="`${row.title} 的更多操作`" @click.prevent.stop><Ellipsis :size="16" :stroke-width="1.75" /></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="draft">传新一稿</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
          <ChevronRight class="chev" :size="16" :stroke-width="1.75" />
        </a>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 入口页（澄明第七节模板 ②）：内容宽 960 */
.review-home { max-width: 960px; margin: 0 auto; padding: 48px 24px 64px; box-sizing: border-box; }
.head { margin-bottom: 28px; }
.entry-lede { margin-bottom: 0; }
.err { margin-bottom: 16px; }
.file-input { display: none; }
/* 大号拖放区：220 高、圆角 18、2px 虚线，模块色图标；拖入时边框变模块色、底色变浅、放大 1.01 */
.drop { position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 220px; padding: 28px 24px; border: 2px dashed var(--si-hair-strong); border-radius: 18px; background: var(--si-surface-1); text-align: center; cursor: pointer; overflow: hidden; transition: border-color var(--si-d2) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out), transform var(--si-d3) var(--si-ease-out); }
.drop:hover { border-color: var(--si-m-review); }
.drop:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.drop.dragging { border-style: solid; border-color: var(--si-m-review); background: var(--si-m-review-soft); transform: scale(1.01); }
.drop.busy { cursor: progress; }
.drop-ic { display: grid; place-items: center; width: 56px; height: 56px; margin-bottom: 4px; border-radius: 16px; background: var(--si-m-review-soft); color: var(--si-m-review); transition: transform var(--si-d3) var(--si-ease-spring); }
.drop:hover .drop-ic, .drop.dragging .drop-ic { transform: translateY(-3px) scale(1.06); }
.drop-title { color: var(--si-ink-1); font: var(--si-font-h3); }
.drop .hint { margin: 0; }
.pick { margin-top: 10px; }
.pick :deep(> span) { gap: 6px; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: var(--si-sunken); }
.bar i { display: block; height: 100%; background: var(--si-progress-flow, var(--si-brand-2)); transition: width var(--si-d3) var(--si-ease-out); }
.recent { margin-top: 48px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.recent h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.all { display: inline-flex; align-items: center; gap: 2px; color: var(--si-ink-3); font: var(--si-font-label-sm); text-decoration: none; }
.all:hover { color: var(--si-blue-ink); }
.list { border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); overflow: hidden; }
.li { display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 10px 16px; color: inherit; text-decoration: none; transition: background-color var(--si-d2) var(--si-ease-out); }
html[data-fx="full"] .li { animation: rh-in var(--si-d3) var(--si-ease-out) both; animation-delay: calc(var(--i) * 45ms + 180ms); }
.li + .li { border-top: 1px solid var(--si-hair-faint); }
.li:hover { background: var(--si-hover-bg); }
.li:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
@keyframes rh-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.mod-ic { flex: none; display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: var(--si-m-review-soft); color: var(--si-m-review); }
.li-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.li-main b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.li-main span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.badge { flex: none; display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 10px; border-radius: 999px; font: var(--si-font-badge); white-space: nowrap; }
.badge .d { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.b-info { background: var(--si-info-soft); color: var(--si-info-ink); }
.b-mid { background: var(--si-warn-soft); color: var(--si-warn-ink); }
.b-hi { background: var(--si-danger-soft); color: var(--si-danger-ink); }
.b-brand { background: var(--si-blue-tint); color: var(--si-blue-ink); }
.b-neu { background: var(--si-surface-2); color: var(--si-ink-3); }
.breath .d { animation: rh-breath 1.6s var(--si-ease-io) infinite; }
@keyframes rh-breath { 50% { opacity: .35; } }
.li-t { flex: none; width: 128px; white-space: nowrap; color: var(--si-ink-4); font: var(--si-font-caption); text-align: right; }
.chev { flex: none; color: var(--si-ink-4); }
.more { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 8px; background: transparent; color: var(--si-ink-3); cursor: pointer; }
.more:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
</style>
