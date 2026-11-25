<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronDown, Download } from '@lucide/vue'
import { canAnnotateRunFile, type RunDetail } from '@/api/runs'
import type { TaskDetail } from '@/api/tasks'
import { useFx } from '@/utils/motion'

/**
 * 分体导出：主按钮“导出报告”，箭头菜单另有问题清单和各文件的批注版。
 * exporting 为正在生成的名称（如“批注版”），savedTick 每次保存成功加一，按钮显示“已保存”1.6 秒。
 */
/** changes：有上一稿的文件（V35-D-02），批注版可同时标出与上一稿的变化，默认勾上 */
const props = defineProps<{ task: TaskDetail; run: RunDetail | null; exportReason: string; exporting: string; deleting: boolean; savedTick: number
  changes?: { seq: number; total: number; untracked: number } | null }>()
const emit = defineEmits<{ report: [format?: 'issue-list']; annotated: [seq: number, includeChanges: boolean] }>()
const withChanges = ref(true)
const fx = useFx()

const localOnly = computed(() => props.task.status === 'completed_local')
const runDone = computed(() => !!props.run && ['completed', 'partial'].includes(props.run.status))
const files = computed(() => localOnly.value ? [] : props.task.files.filter(f => canAnnotateRunFile(props.run, f)))
const fileReason = computed(() => props.exportReason || (runDone.value ? '' : '本轮审核未完成，不能生成'))

const saved = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
watch(() => props.savedTick, () => { saved.value = true; clearTimeout(timer); timer = setTimeout(() => { saved.value = false }, 1600) })
onBeforeUnmount(() => clearTimeout(timer))
const label = computed(() => props.exporting ? `正在生成${props.exporting}…` : saved.value ? '已保存' : '导出报告')

function onCommand(command: string) {
  const [kind, seq] = command.split(':')
  if (kind === 'report') emit('report')
  else if (kind === 'issue-list') emit('report', 'issue-list')
  else if (kind === 'annotated') emit('annotated', Number(seq), props.changes?.seq === Number(seq) && withChanges.value)
}
</script>

<template>
  <div class="export-split">
    <el-button class="main" :disabled="!!exportReason || deleting" :title="exportReason || undefined" @click="!exporting && emit('report')">
      <span v-if="exporting && fx !== 'off'" class="progress" aria-hidden="true" />
      <Download :size="16" :stroke-width="1.75" class="icon" /><span class="label" aria-live="polite">{{ label }}</span>
    </el-button>
    <el-dropdown trigger="click" placement="bottom-end" :disabled="deleting || !!exporting" @command="onCommand">
      <el-button class="arrow" :disabled="deleting || !!exporting" aria-label="更多导出方式"><ChevronDown :size="16" :stroke-width="1.75" /></el-button>
      <template #dropdown>
        <el-dropdown-menu class="export-menu">
          <el-dropdown-item command="report" :disabled="!!exportReason">
            <div class="item"><span class="name">{{ localOnly ? '本地校验报告' : '审核报告' }}</span><span class="sub">{{ exportReason || 'Word · 完整审核记录' }}</span></div>
          </el-dropdown-item>
          <el-dropdown-item v-if="!localOnly" command="issue-list" :disabled="!!fileReason">
            <div class="item"><span class="name">问题清单</span><span class="sub">{{ fileReason || 'Word · 原页码与条款、问题与依据、建议条文' }}</span></div>
          </el-dropdown-item>
          <template v-for="file in files" :key="file.id">
            <el-dropdown-item :command="`annotated:${file.seq}`" :disabled="!!fileReason">
              <div class="item"><span class="name" :title="file.original_name">批注版：{{ file.original_name }}</span><span class="sub">{{ fileReason || '原件不改，问题挂在批注' }}</span></div>
            </el-dropdown-item>
            <li v-if="changes && changes.seq === file.seq && changes.total" class="changes-opt" @click.stop>
              <el-checkbox v-model="withChanges" size="small">同时标出与上一稿的变化（{{ changes.total }} 处<template v-if="changes.untracked">，未留痕 {{ changes.untracked }} 处</template>）</el-checkbox>
            </li>
          </template>
          <el-dropdown-item v-if="!localOnly" disabled divided><span class="hint-line">批注版支持 Word 与 WPS 文字文件</span></el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<style scoped>
.export-split { display: inline-flex; }
.main { position: relative; overflow: hidden; border-top-right-radius: 0; border-bottom-right-radius: 0; }
.arrow { margin-left: -1px; padding: 0 8px; border-top-left-radius: 0; border-bottom-left-radius: 0; }
.icon { margin-right: 6px; }
.label { position: relative; font-variant-numeric: tabular-nums; }
.progress { position: absolute; inset: 0; background: color-mix(in srgb, var(--el-color-primary) 20%, transparent); transform-origin: left; transform: scaleX(0); animation: export-progress 1.2s var(--si-ease) forwards; }
@keyframes export-progress { to { transform: scaleX(.9); } }
.item { display: flex; flex-direction: column; max-width: 360px; padding: 2px 0; line-height: 1.4; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-label); }
.sub { font: var(--si-font-caption); color: var(--si-ink-3); white-space: normal; }
.changes-opt { list-style: none; padding: 0 16px 6px 20px; }
.hint-line { max-width: 360px; white-space: normal; font: var(--si-font-caption); color: var(--si-ink-3); }
</style>
