<script setup lang="ts">
import { computed } from 'vue'
import { Ellipsis, Trash2 } from '@lucide/vue'
import type { TaskDetail } from '@/api/tasks'
import type { RunDetail, RunListItem } from '@/api/runs'
import ExportButton from '@/components/result/ExportButton.vue'
import ReceiptStrip from '@/components/result/ReceiptStrip.vue'
import { TEMPLATE_STATE, RUN_STATUS_TAG, docTypeLabel, runStatusLabel, sourceLabel, stanceLabel, statusLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

/** 结果页工具条（V31-U3 3.2）：第一行文件、轮次、回执与操作；第二行次要信息，单行省略、悬停看全文。 */
const props = defineProps<{ task: TaskDetail; run: RunDetail | null; rounds: RunListItem[]; selectedRun: string; reqVersion: string; warnings: string[]; exportReason: string; exporting: string; savedTick: number; deleting: boolean }>()
const emit = defineEmits<{ selectRun: [id: string]; exportReport: []; exportAnnotated: [seq: number]; exportRevised: [seq: number]; remove: [] }>()

const mainFile = computed(() => props.task.files.find(f => f.is_main) ?? props.task.files[0])
const badge = computed(() => {
  const ext = mainFile.value?.ext ?? ''
  return ['docx', 'doc', 'wps', 'rtf'].includes(ext) ? 'DOC' : ['xlsx', 'xls', 'et'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'IMG'
})
const modelText = computed(() => props.run?.model_label ? `${props.run.model_label}（${props.run.model_name ?? '—'}）` : props.run?.model_name ?? '—')
const details = computed(() => {
  const r = props.run
  const parts = [
    docTypeLabel(r?.main_doc_type ?? mainFile.value?.doc_type),
    `${stanceLabel(r?.stance ?? props.task.stance)} / ${sourceLabel(r?.source ?? props.task.source)}`,
  ]
  if (r) parts.push(`审核于 ${formatDateTime(r.finished_at)}`, `模型 ${modelText.value}`, `要求 ${props.reqVersion}`)
  if (r?.template) parts.push(`本轮对照公司范本：${r.template.name}${TEMPLATE_STATE[r.template.status] ?? ''}`)
  return parts.join(' · ')
})
</script>

<template>
  <header class="toolbar">
    <div class="top">
      <el-button link type="primary" @click="$router.push('/history')">← 审核历史</el-button>
      <span class="separator" aria-hidden="true"></span>
      <span class="file-badge">{{ badge }}</span>
      <div class="file-name" :title="mainFile?.original_name ?? task.title">{{ mainFile?.original_name ?? task.title }}</div>
      <span v-if="task.files.length > 1" class="hint nowrap">另有 {{ task.files.length - 1 }} 个附件</span>
      <el-tag v-if="run" :type="RUN_STATUS_TAG[run.status] ?? 'info'" size="small">{{ runStatusLabel(run.status) }}</el-tag>
      <span v-else class="hint nowrap">{{ statusLabel(task.status) }}</span>
      <el-select v-if="rounds.length > 1" :model-value="selectedRun" aria-label="审核轮次" class="round-select" size="small" @update:model-value="emit('selectRun', $event)">
        <el-option v-for="item in rounds" :key="item.id" :value="item.id" :label="`第 ${item.run_no} 轮 · ${formatDateTime(item.finished_at ?? item.queued_at)} · ${runStatusLabel(item.status)}`" />
      </el-select>
      <ReceiptStrip v-if="run && task.policy === 'ai'" :run="run" :file-names="task.files.map(f => f.original_name)" />
      <div class="actions">
        <slot name="before-export" />
        <ExportButton :task="task" :run="run" :export-reason="exportReason" :exporting="exporting" :deleting="deleting" :saved-tick="savedTick"
          @report="emit('exportReport')" @annotated="emit('exportAnnotated', $event)" @revised="emit('exportRevised', $event)" />
        <slot />
        <el-dropdown trigger="click" placement="bottom-end" :disabled="deleting" @command="emit('remove')">
          <el-button text :loading="deleting" aria-label="更多操作"><Ellipsis :size="16" :stroke-width="1.75" /></el-button>
          <template #dropdown>
            <el-dropdown-menu><el-dropdown-item command="remove" class="danger-item"><Trash2 :size="14" :stroke-width="1.75" />删除任务</el-dropdown-item></el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
    <div class="details" :title="[details, ...warnings].join(' · ')">
      {{ details }}<template v-for="warning in warnings" :key="warning"> · <span class="warning">{{ warning }}</span></template>
    </div>
  </header>
</template>

<style scoped>
.toolbar { flex: none; padding: 6px 0 8px; border-bottom: 1px solid var(--si-hair); }
.top { display: flex; align-items: center; gap: 10px; min-height: 48px; }
.separator { flex: none; width: 1px; height: 24px; background: var(--si-hair); }
.file-badge { flex: none; width: 32px; height: 32px; display: grid; place-items: center; border-radius: 8px; color: var(--el-color-primary); background: var(--el-color-primary-light-9); font: var(--si-font-label-sm); }
.file-name { min-width: 80px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h2); }
.nowrap { white-space: nowrap; }
.round-select { flex: none; width: 200px; }
.actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
.details { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--si-ink-3); font: var(--si-font-caption); }
.warning { color: var(--si-warn-ink); }
.danger-item { gap: 6px; color: var(--el-color-danger); }
</style>
