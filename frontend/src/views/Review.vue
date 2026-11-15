<script setup lang="ts">
// 文件审核首页（V34-A-03，visual.md 7.4）：一句说明 + 拖入区 + 最近的审核。
// 拖入、点选或粘贴文件才建会话（不在进入页面时建空会话），上传后进入该会话的审核视图；底层仍是“工作空间就是会话”。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { UploadCloud } from '@lucide/vue'
import { assistantApi, type HistoryItem } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { usePasteFiles } from '@/composables/usePasteFiles'
import { useSessionsStore } from '@/stores/sessions'
import { statusLabel } from '@/utils/labels'
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
function drop(event: DragEvent) { dragging.value = false; void upload(Array.from(event.dataTransfer?.files ?? [])) }
usePasteFiles(files => void upload(files), () => uploading.value === null)

// 最近的审核：历史记录第一页中发起过审核的会话与旧审核任务，取前 5 条
onMounted(async () => {
  try { recent.value = (await assistantApi.history({ page: 1 })).items.filter(row => row.kind === 'review' || row.run_count > 0).slice(0, 5) }
  catch { /* 列表失败不影响上传 */ }
})
const rows = computed(() => recent.value.map(row => ({
  row,
  state: row.reviewing ? '审核中' : row.kind === 'session' ? `已审核 ${row.run_count} 轮` : statusLabel(row.status),
  pending: !row.reviewing && row.latest_run_id && row.pending_count > 0 ? `待处理 ${row.pending_count}` : '',
})))
function open(row: HistoryItem) {
  if (row.kind === 'session') void router.push({ path: `/assistant/${row.id}`, query: { mode: 'review' } })
  else void router.push(row.status === 'draft' ? { name: 'review-new', query: { task: row.id } } : { name: 'review-result', params: { taskId: row.id } })
}
</script>

<template>
  <div class="page page--standard review-home">
    <h1 class="entry-title">要审核什么文件？</h1>
    <p class="entry-lede">合同、报价单、订单、函件都可以。文件在本机解析和脱敏，你确认后才交给模型初审。</p>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div class="si-drop" :class="{ dragging }" role="button" tabindex="0" :aria-disabled="uploading !== null" @click="picker?.click()" @keydown.enter="picker?.click()" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop">
      <UploadCloud :size="28" :stroke-width="1.5" />
      <p class="si-drop-title">{{ uploading !== null ? `正在上传 ${uploading}%` : '拖入要审核的文件，或点击选择' }}</p>
      <p class="hint">Word、PDF、Excel、WPS、图片，可一次加入多份；也可以 Ctrl+V 粘贴</p>
    </div>
    <input ref="picker" class="file-input" type="file" multiple :accept="DOC" @change="picked">
    <section v-if="rows.length" class="recent">
      <div class="recent-head"><h2>最近的审核</h2><router-link to="/history" class="all">全部历史</router-link></div>
      <a v-for="{ row, state, pending } in rows" :key="row.id" href="#" class="list-row" @click.prevent="open(row)">
        <span class="name">{{ row.title }}</span>
        <span class="meta hint num">{{ state }}<template v-if="pending"> · <em>{{ pending }}</em></template> · {{ formatDateTime(row.last_activity_at) }}</span>
      </a>
    </section>
  </div>
</template>

<style scoped>
.file-input { display: none; }
.si-drop[aria-disabled="true"] { cursor: progress; }
.recent { margin-top: 32px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.recent h2 { margin: 0; font: var(--si-font-h3); }
.all { font: var(--si-font-caption); color: var(--si-ink-3); text-decoration: none; }
.all:hover { color: var(--si-ink-1); }
.meta { flex: none; margin: 0; }
.meta em { font-style: normal; color: var(--si-ink-1); }
</style>
