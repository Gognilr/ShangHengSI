<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sharedContentApi, type SharedContent, type SharedContentKind, type SharedReuseStatus } from '@/api/sharedContent'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import { docTypeLabel, stanceLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const props = defineProps<{ kind: SharedContentKind }>()
const rows = ref<SharedContent[]>([])
const loading = ref(true)
const error = ref('')
const removing = ref(''), retrying = ref('')
const reuseLabels: Record<SharedReuseStatus, string> = { preparing: '准备中', ready: '可复用', failed: '准备失败' }
const reuseTypes = { preparing: 'info', ready: 'success', failed: 'warning' } as const
let active = true, background = false, revision = 0, timer: ReturnType<typeof setTimeout> | undefined

function schedule() {
  clearTimeout(timer)
  if (active && rows.value.some(r => r.reuse_status === 'preparing')) timer = setTimeout(() => void load(false), 3000)
}

function scope(row: SharedContent) {
  if (props.kind === 'bid') return row.category ?? '分类已移除'
  return [row.doc_type && docTypeLabel(row.doc_type), row.stance && stanceLabel(row.stance)].filter(Boolean).join(' · ')
}

async function load(showLoading = true) {
  if (background || retrying.value || removing.value) { schedule(); return }
  background = true
  const current = revision
  if (showLoading) { error.value = ''; loading.value = true }
  try {
    const result = await sharedContentApi.list(props.kind)
    if (active && current === revision) rows.value = result.items
  } catch (e) {
    if (active && showLoading) error.value = e instanceof ApiError ? e.message : '加载失败，请重试'
  } finally { background = false; loading.value = false; schedule() }
}

async function retry(row: SharedContent) {
  if (retrying.value || removing.value) return
  ++revision
  retrying.value = row.id
  try {
    const result = await sharedContentApi.retry(row.id)
    const current = rows.value.find(item => item.id === row.id)
    if (current) current.reuse_status = result.reuse_status
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '重新准备失败，请重试') }
  finally { retrying.value = ''; schedule() }
}

async function remove(row: SharedContent) {
  if (removing.value || retrying.value) return
  try {
    await ElMessageBox.confirm('撤下后将停止在后续审核中复用这条内容。', '撤下公司沉淀', {
      confirmButtonText: '撤下', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  ++revision
  removing.value = row.id
  try {
    await sharedContentApi.remove(props.kind, row.id)
    rows.value = rows.value.filter(item => item.id !== row.id)
    ElMessage.success('已撤下')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '撤下失败，请重试') }
  finally { removing.value = ''; schedule() }
}

onMounted(() => void load())
onBeforeUnmount(() => { active = false; clearTimeout(timer) })
</script>

<template>
  <div :aria-busy="loading">
    <p v-if="loading" role="status" class="meta">正在读取公司沉淀</p>
    <div v-else-if="error" class="error-row" role="alert">
      <span>{{ error }}</span><el-button link type="primary" @click="load()">重试</el-button>
    </div>
    <EmptyState v-else-if="!rows.length" compact title="暂无公司沉淀" />
    <ul v-else class="shared-list">
      <li v-for="row in rows" :key="row.id" class="shared-row">
        <div class="shared-main">
          <p class="meta">{{ scope(row) }} · {{ formatDateTime(row.shared_at) }}
            <el-tag v-if="row.reuse_status" :type="reuseTypes[row.reuse_status]" size="small" effect="plain">{{ reuseLabels[row.reuse_status] }}</el-tag>
          </p>
          <p class="shared-text">{{ row.text }}</p>
        </div>
        <div class="shared-actions">
          <el-button v-if="row.reuse_status === 'failed'" link type="primary" :loading="retrying === row.id" :disabled="!!removing || !!retrying"
            @click="retry(row)">重新准备</el-button>
          <el-button link type="danger" :loading="removing === row.id" :disabled="!!retrying || (!!removing && removing !== row.id)"
            @click="remove(row)">撤下</el-button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.shared-list { list-style: none; padding: 0; margin: 0; }
.shared-row { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--si-hair); }
.shared-main { min-width: 0; }
.shared-actions { display: flex; flex: none; }
.meta { margin: 6px 0; color: var(--si-ink-3); font: var(--si-font-label); }
.shared-text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; color: var(--si-ink-1); font: var(--si-font-body); }
.error-row { display: flex; align-items: center; gap: 12px; color: var(--si-danger-ink); }
</style>
