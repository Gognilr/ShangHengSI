<script setup lang="ts">
import { ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { localFindingsApi, type LocalFinding } from '@/api/localFindings'
import { localCheckLabel, SEVERITY_TAG, severityLabel } from '@/utils/labels'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{
  (e: 'count', count: number | null): void
  (e: 'select', locs: string[]): void
}>()
const items = ref<LocalFinding[]>([])
const loading = ref(false)
const error = ref('')
let version = 0

async function load() {
  const current = ++version
  loading.value = true
  error.value = ''
  items.value = []
  emit('count', null)
  try {
    const result = await localFindingsApi.list(props.taskId)
    if (current !== version) return
    items.value = result.items
    emit('count', result.items.length)
  } catch (e) {
    if (current !== version) return
    error.value = e instanceof ApiError
      ? e.status === 404 ? '任务不存在或已被删除' : `${e.message}，请刷新页面后重试`
      : '加载失败，请刷新页面后重试'
  } finally {
    if (current === version) loading.value = false
  }
}

function select(row: LocalFinding) {
  if (row.locs.length) emit('select', [...row.locs])
}
watch(() => props.taskId, load, { immediate: true })
</script>

<template>
  <div v-loading="loading" class="local-findings">
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
    <el-table v-else :data="items" size="small" empty-text="本地校验未发现问题"
      :row-class-name="({ row }: { row: LocalFinding }) => row.locs.length ? 'locatable' : ''"
      @row-click="select">
      <el-table-column label="严重程度" width="76">
        <template #default="{ row }">
          <el-tag :type="SEVERITY_TAG[row.severity] ?? 'info'" effect="dark" size="small">{{ severityLabel(row.severity) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="校验项" min-width="90">
        <template #default="{ row }">{{ localCheckLabel(row.check_code) }}</template>
      </el-table-column>
      <el-table-column prop="location" label="位置" min-width="100" />
      <el-table-column label="说明" min-width="140">
        <template #default="{ row }"><span class="detail">{{ row.detail }}</span></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.local-findings { min-height: 64px; border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); overflow: hidden; }
.detail { white-space: pre-wrap; overflow-wrap: anywhere; }
:deep(.cell) { overflow-wrap: anywhere; }
:deep(.locatable) { cursor: pointer; }
</style>
