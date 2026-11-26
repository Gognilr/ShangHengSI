<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 文件审核首页（V34-A-03；澄明二版 6.6）：标题与一句说明 + 纸叠拖放区 + 按天的最近审核 5 条。
// 拖入、点选或粘贴文件才建会话（不在进入页面时建空会话），上传后进入该会话的审核视图；底层仍是“工作空间就是会话”。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronRight, Ellipsis, FileText, Upload } from '@lucide/vue'
import { assistantApi, type HistoryItem } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { usePasteFiles } from '@/composables/usePasteFiles'
import { useSessionsStore } from '@/stores/sessions'
import SiIllus from '@/components/brand/illus/SiIllus.vue'
import TripTimeline, { type TimelineItem } from '@/components/TripTimeline.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import { MAX_FILE_MB } from '@/api/tasks'
import { docTypeLabel, stanceLabel } from '@/utils/labels'
import { rowTrip, type Trip } from '@/utils/trip'

const router = useRouter(), sessions = useSessionsStore()
const DOC = '.docx,.pdf,.xlsx,.doc,.wps,.rtf,.xls,.et,.jpg,.jpeg,.png'
const picker = ref<HTMLInputElement>(), dragging = ref(false)
const uploading = ref<number | null>(null), error = ref('')
const recent = ref<HistoryItem[]>([])

async function upload(files: File[]) {
  if (!files.length || uploading.value !== null) return
  if (files.some(f => /\.(mp3|m4a|wav|amr|aac|mp4)$/i.test(f.name))) { error.value = '录音请到“会议纪要”中处理'; return }
  error.value = ''; uploading.value = 0
  incoming.value = files.map(f => ({ name: f.name, size: sizeText(f.size) }))
  try {
    const created = await assistantApi.create('chat', true)
    await assistantApi.addFiles(created.id, files, (loaded, total) => { uploading.value = Math.round(loaded / total * 100) })
    sessions.markReview(created.id)
    await router.push({ path: `/assistant/${created.id}`, query: { mode: 'review' } })
    void sessions.refresh()
  } catch (e) { error.value = e instanceof ApiError ? e.message : '上传失败，请稍后重试'; incoming.value = [] }
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
  try { recent.value = (await assistantApi.history({ page: 1, type: 'review' })).items.slice(0, 5) }
  catch { /* 列表失败不影响上传 */ }
})
// 每行（澄明二版 6.6）：按天的时间线，迷你往返线说进度，徽标只留结果的计数
const rows = computed<(TimelineItem & { row: HistoryItem })[]>(() => recent.value.map(row => {
  const trip = rowTrip(row)
  const finished = trip.state === 'done' || trip.state === 'partial'
  const badge = !finished ? null : row.high_count > 0 ? { text: `高 ${row.high_count}`, tone: 'danger' as const, dot: true }
    : row.latest_run_id && row.pending_count > 0 ? { text: `待处理 ${row.pending_count}`, tone: 'neutral' as const } : null
  const sub = [row.doc_type ? docTypeLabel(row.doc_type) : '', row.stance ? `${stanceLabel(row.stance)}立场` : '', row.run_count > 1 ? `第 ${row.run_count} 轮` : '', row.file_count > 1 ? `${row.file_count} 份文件` : ''].filter(Boolean).join(' · ')
  return { key: row.id, title: row.title, sub, trip, time: row.last_activity_at, mod: 'review' as const, badge, row }
}))
// 纸叠里正在上传的文件：从“读取”开始走
const incoming = ref<{ name: string; size: string }[]>([])
const incomingTrip = computed<Trip>(() => ({ mod: 'review', at: 0, state: 'run', note: uploading.value != null && uploading.value < 100 ? `上传 ${uploading.value}%` : '正在读取' }))
const sizeText = (n: number) => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
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
      <p class="entry-lede">合同、报价单、订单、函件都可以。原件只在本机读取，名称遮住、你确认后才交给模型。</p>
    </header>
    <SiNotice v-if="error" type="error" :closable="false" :title="error" class="err" />
    <!-- 纸叠（二版 6.6）：拖入时最上一张抬起；松手后纸滑进来，下方出现这份文件的迷你往返线 -->
    <div class="pile si-hov" :class="{ dragging, busy: uploading !== null }" role="button" tabindex="0" :aria-disabled="uploading !== null" aria-label="选择要审核的文件"
      @click="uploading === null && picker?.click()" @keydown.enter.self="uploading === null && picker?.click()" @dragenter.prevent="dragEnter" @dragover.prevent @dragleave="dragLeave" @drop.prevent="drop">
      <SiIllus kind="stack" :on="dragging || uploading !== null" class="pile-obj" />
      <div class="pile-b">
        <h2>{{ dragging ? '松开即可加入' : '把文件放到这叠纸上' }}</h2>
        <p class="ph">也可以点击选择，或按 Ctrl+V 粘贴。一次可以放多份，会在同一次审核里一起看。</p>
        <div class="pa"><el-button type="primary" size="large" :disabled="uploading !== null" @click.stop="picker?.click()"><Upload :size="16" :stroke-width="1.75" />选择文件</el-button></div>
        <p class="pf">Word、PDF、扫描件、图片、Excel、WPS · 单个不超过 {{ MAX_FILE_MB }} MB</p>
        <TransitionGroup v-if="incoming.length" name="pile-in" tag="ul" class="pile-in" aria-live="polite">
          <li v-for="f in incoming" :key="f.name" class="pfile">
            <span class="mod-ic"><FileText :size="16" :stroke-width="1.75" /></span>
            <span class="pf-n"><b :title="f.name">{{ f.name }}</b><span class="num">{{ f.size }}</span></span>
            <SiTrip :trip="incomingTrip" />
          </li>
        </TransitionGroup>
      </div>
    </div>
    <input ref="picker" class="file-input" type="file" multiple :accept="DOC" @change="picked">
    <input ref="draftPicker" class="file-input" type="file" :accept="DOC" @change="draftPicked">
    <section v-if="rows.length" class="recent">
      <div class="recent-head"><h2>最近的审核</h2><router-link to="/history" class="all">全部历史<ChevronRight :size="14" :stroke-width="1.75" /></router-link></div>
      <TripTimeline :items="rows" @open="it => open(it.row)">
        <template #actions="{ item }">
          <el-dropdown v-if="item.row.kind === 'session'" trigger="click" @command="() => pickDraft(item.row)">
            <button type="button" class="more" :aria-label="`${item.title} 的更多操作`"><Ellipsis :size="16" :stroke-width="1.75" /></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="draft">传新一稿</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </TripTimeline>
    </section>
  </div>
</template>

<style scoped>
/* 入口页（澄明第七节模板 ②）：内容宽 960 */
.review-home { max-width: 960px; margin: 0 auto; padding: 48px 24px 64px; box-sizing: border-box; }
.head { margin-bottom: 24px; }
.entry-lede { margin-bottom: 0; }
.err { margin-bottom: 16px; }
.file-input { display: none; }
/* 纸叠：左插画 300、右说明；拖入时描边变衡青并带浅光晕 */
.pile { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 32px; align-items: center; padding: 30px 32px 26px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); cursor: pointer; transition: box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d3) var(--si-ease-out); }
.pile:hover { box-shadow: var(--si-shadow-2); }
.pile:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.pile.dragging { border-color: var(--si-brand-line); box-shadow: 0 0 0 4px var(--si-brand-soft), var(--si-shadow-2); }
.pile.busy { cursor: progress; }
.pile-obj { height: 170px; }
.pile h2 { margin: 0; color: var(--si-ink-1); font: var(--si-font-h2); font-size: calc(22px * var(--si-fs)); line-height: 1.4; }
.ph { margin: 6px 0 0; color: var(--si-ink-3); font: var(--si-font-body); }
.pa { display: flex; align-items: center; gap: 10px; margin-top: 18px; }
.pa :deep(.el-button > span) { gap: 6px; }
.pf { margin: 16px 0 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.pile-in { display: flex; flex-direction: column; gap: 8px; margin: 12px 0 0; padding: 0; list-style: none; }
.pfile { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-md); background: var(--si-card-2); }
.pile-in-enter-active { transition: transform 500ms var(--si-ease-out), opacity 500ms var(--si-ease-out); }
.pile-in-enter-from { opacity: 0; transform: translateY(-14px); }
html[data-fx="off"] .pile-in-enter-active { transition: none; }
.mod-ic { flex: none; display: grid; place-items: center; width: 32px; height: 32px; border-radius: var(--si-radius-control); background: var(--si-m-review-soft); color: var(--si-m-review); }
.pf-n { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.pf-n b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.pf-n span { color: var(--si-ink-3); font: var(--si-font-caption); }
.recent { margin-top: 44px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.recent h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.all { display: inline-flex; align-items: center; gap: 2px; color: var(--si-ink-3); font: var(--si-font-label-sm); text-decoration: none; }
.all:hover { color: var(--si-blue-ink); }
.more { display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.more:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
@media (max-width: 900px) { .pile { grid-template-columns: minmax(0, 1fr); } .pile-obj { height: 120px; } }
</style>
