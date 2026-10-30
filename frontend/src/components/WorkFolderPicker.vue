<script setup lang="ts">
import { ref, watch } from 'vue'
import { FolderOpen } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import { listFiles, type FolderFile } from '@/utils/workFolder'
import { formatDateTime } from '@/utils/time'

// 列出工作文件夹第一层中支持格式的文件，勾选后点“加入”才读取并交给上传；打开本窗口本身不上传任何内容（V3-A 7.2）
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ add: [files: File[]] }>()
const rows = ref<FolderFile[]>([])
const selected = ref<FolderFile[]>([])
const loading = ref(false)
const error = ref('')

watch(open, async value => {
  if (!value) return
  rows.value = []; selected.value = []; error.value = ''; loading.value = true
  try { rows.value = await listFiles() }
  catch (e) { error.value = e instanceof Error && e.message ? e.message : '无法读取工作文件夹' }
  finally { loading.value = false }
})

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}
function add() {
  emit('add', selected.value.map(row => row.file))
  open.value = false
}
</script>

<template>
  <el-dialog v-model="open" title="从工作文件夹选择" width="640px" destroy-on-close>
    <el-alert v-if="error" type="error" show-icon :closable="false" :title="error" />
    <el-table v-else v-loading="loading" :data="rows" max-height="360" @selection-change="selected = $event">
      <template #empty><EmptyState compact :icon="FolderOpen" title="文件夹里没有支持的文件" description="只列出第一层中 DOCX、PDF、XLSX 等系统支持格式的文件。" /></template>
      <el-table-column type="selection" width="44" />
      <el-table-column prop="name" label="文件名" min-width="220" show-overflow-tooltip />
      <el-table-column label="大小" width="90"><template #default="{ row }"><span class="num">{{ formatSize(row.size) }}</span></template></el-table-column>
      <el-table-column label="修改时间" width="160"><template #default="{ row }"><span class="num">{{ formatDateTime(new Date(row.modified).toISOString()) }}</span></template></el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button type="primary" :disabled="!selected.length" @click="add">加入{{ selected.length ? `（${selected.length}）` : '' }}</el-button>
    </template>
  </el-dialog>
</template>
