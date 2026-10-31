<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, Download } from '@lucide/vue'
import type { TaskDetail } from '@/api/tasks'
import type { RunDetail, RunListItem } from '@/api/runs'
import { TEMPLATE_STATE, RUN_STATUS_TAG, docTypeLabel, runStatusLabel, sourceLabel, stanceLabel, statusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const props = defineProps<{ task: TaskDetail; run: RunDetail | null; rounds: RunListItem[]; selectedRun: string; reqVersion: string; warnings: string[]; exportReason: string; exporting: boolean; deleting: boolean }>()
const emit = defineEmits<{ selectRun: [id: string]; exportReport: []; exportAnnotated: [seq: number] }>()
const annotatedFiles = computed(() => props.run && ['completed', 'partial'].includes(props.run.status)
  ? props.task.files.filter(file => file.annotatable) : [])

function exportCommand(command: string | number) {
  if (command === 'report') emit('exportReport')
  else if (typeof command === 'number') emit('exportAnnotated', command)
}

function badge(ext: string) {
  return ['docx', 'doc', 'wps', 'rtf'].includes(ext) ? 'DOC' : ['xlsx', 'xls', 'et'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'IMG'
}
</script>

<template>
  <header class="toolbar">
    <div class="top">
      <el-button link type="primary" @click="$router.push('/history')">← 审核历史</el-button>
      <span class="separator" aria-hidden="true"></span>
      <span class="file-badge">{{ badge((task.files.find(f => f.is_main) ?? task.files[0])?.ext ?? '') }}</span>
      <div class="file-name" :title="(task.files.find(f => f.is_main) ?? task.files[0])?.original_name ?? task.title">{{ (task.files.find(f => f.is_main) ?? task.files[0])?.original_name ?? task.title }}</div>
      <span v-if="task.files.length > 1" class="hint">另有 {{ task.files.length - 1 }} 个附件</span>
      <el-tag v-if="run" :type="RUN_STATUS_TAG[run.status] ?? 'info'" size="small">{{ runStatusLabel(run.status) }}</el-tag>
      <span v-else class="hint">{{ statusLabel(task.status) }}</span>
      <el-select v-if="rounds.length > 1" :model-value="selectedRun" aria-label="审核轮次" class="round-select" size="small" @update:model-value="emit('selectRun', $event)">
        <el-option v-for="item in rounds" :key="item.id" :value="item.id" :label="`第 ${item.run_no} 轮 · ${formatDateTime(item.finished_at ?? item.queued_at)} · ${runStatusLabel(item.status)}`" />
      </el-select>
      <span v-else-if="run" class="hint">第 {{ run.run_no }} 轮</span>
      <div class="actions">
        <slot name="before-export" />
        <el-dropdown trigger="click" :disabled="!!exportReason || exporting || deleting" @command="exportCommand">
          <el-button :loading="exporting" :disabled="!!exportReason || deleting">
            <template #icon><Download :size="16" :stroke-width="1.75" /></template>
            导出<ChevronDown :size="16" :stroke-width="1.75" />
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="report">审核报告</el-dropdown-item>
              <el-dropdown-item v-for="file in annotatedFiles" :key="file.id" :command="file.seq">
                <span class="export-file" :title="`批注版：${file.original_name}`">批注版：{{ file.original_name }}</span>
              </el-dropdown-item>
              <el-dropdown-item disabled divided class="export-hint">批注版支持 Word 与 WPS 文字文件；PDF、图片、表格只能导出审核报告</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <slot />
      </div>
    </div>
    <div class="details">
      <span>{{ docTypeLabel(run?.main_doc_type ?? (task.files.find(f => f.is_main) ?? task.files[0])?.doc_type) }} · {{ stanceLabel(run?.stance ?? task.stance) }} / {{ sourceLabel(run?.source ?? task.source) }} · 审核于 {{ formatDateTime(run?.finished_at) }} · 模型 {{ run?.model_label ? `${run.model_label}（${run.model_name ?? '—'}）` : run?.model_name ?? '—' }} · 要求 {{ reqVersion }}<template v-if="run?.template"> · 本轮对照公司范本：{{ run.template.name }}{{ TEMPLATE_STATE[run.template.status] ?? '' }}</template><template v-for="warning in warnings" :key="warning"> · <span class="warning">{{ warning }}</span></template></span>
      <span v-if="exportReason" class="hint">{{ exportReason }}</span>
    </div>
  </header>
</template>

<style scoped>
.toolbar { flex: none; padding: 12px 0; border-bottom: 1px solid var(--el-border-color-light); background: var(--el-bg-color); }
.top, .details { display: flex; align-items: center; gap: 12px; }
.top { flex-wrap: wrap; }
.separator { width: 1px; height: 24px; background: var(--el-border-color-light); }
.file-badge { flex: none; width: 40px; height: 40px; display: grid; place-items: center; border-radius: 8px; color: var(--el-color-primary); background: var(--el-color-primary-light-9); font: var(--si-font-caption); }
.file-name { min-width: 0; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h2); }
.round-select { width: 200px; }
.actions { display: flex; align-items: center; gap: 8px; margin: 0 0 0 auto; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
.details { justify-content: space-between; flex-wrap: wrap; margin-top: 8px; color: var(--el-text-color-secondary); font: var(--si-font-meta); }
.warning { color: var(--el-color-warning); }
.export-file { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.export-hint { max-width: 360px; white-space: normal; color: var(--el-text-color-secondary); font: var(--si-font-caption); }
</style>
