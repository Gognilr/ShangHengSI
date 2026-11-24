<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sharedContentApi, type SharedContent, type SharedContentKind } from '@/api/sharedContent'
import { ApiError } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'
import { docTypeLabel, stanceLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const props = defineProps<{ kind: SharedContentKind }>()
const rows = ref<SharedContent[]>([])
const loading = ref(true)
const error = ref('')
const removing = ref('')

function scope(row: SharedContent) {
  if (props.kind === 'bid') return row.category ?? '分类已移除'
  return [row.doc_type && docTypeLabel(row.doc_type), row.stance && stanceLabel(row.stance)].filter(Boolean).join(' · ')
}

async function load() {
  error.value = ''
  loading.value = true
  try { rows.value = (await sharedContentApi.list(props.kind)).items }
  catch (e) { error.value = e instanceof ApiError ? e.message : '加载失败，请重试' }
  finally { loading.value = false }
}

async function remove(row: SharedContent) {
  if (removing.value) return
  try {
    await ElMessageBox.confirm('撤下后将停止在后续审核中复用这条内容。', '撤下公司沉淀', {
      confirmButtonText: '撤下', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  removing.value = row.id
  try {
    await sharedContentApi.remove(props.kind, row.id)
    rows.value = rows.value.filter(item => item.id !== row.id)
    ElMessage.success('已撤下')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '撤下失败，请重试') }
  finally { removing.value = '' }
}

onMounted(load)
</script>

<template>
  <div :aria-busy="loading">
    <p v-if="loading" role="status" class="meta">正在读取公司沉淀</p>
    <div v-else-if="error" class="error-row" role="alert">
      <span>{{ error }}</span><el-button link type="primary" @click="load">重试</el-button>
    </div>
    <EmptyState v-else-if="!rows.length" compact title="暂无公司沉淀" />
    <ul v-else class="shared-list">
      <li v-for="row in rows" :key="row.id" class="shared-row">
        <div class="shared-main">
          <p class="meta">{{ scope(row) }} · {{ formatDateTime(row.shared_at) }}</p>
          <p class="shared-text">{{ row.text }}</p>
        </div>
        <el-button link type="danger" :loading="removing === row.id" :disabled="!!removing && removing !== row.id"
          @click="remove(row)">撤下</el-button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.shared-list { list-style: none; padding: 0; margin: 0; }
.shared-row { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--si-hair); }
.shared-main { min-width: 0; }
.meta { margin: 6px 0; color: var(--si-ink-3); font: var(--si-font-label); }
.shared-text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; color: var(--si-ink-1); font: var(--si-font-body); }
.error-row { display: flex; align-items: center; gap: 12px; color: var(--si-danger-ink); }
</style>
