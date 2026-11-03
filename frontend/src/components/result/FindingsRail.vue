<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ListChecks } from '@lucide/vue'
import type { LocalFinding } from '@/api/localFindings'
import type { Finding, RunDetail } from '@/api/runs'
import EmptyState from '@/components/EmptyState.vue'
import FindingCard from '@/components/FindingCard.vue'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import RailSummary from '@/components/result/RailSummary.vue'
import RequirementList from '@/components/result/RequirementList.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import { CATEGORY_OPTIONS, HANDLING_OPTIONS, toOptions } from '@/utils/labels'
import { useFx } from '@/utils/motion'

/** 问题栏（V31-U3 3.4～3.6）：压缩结论 + “AI 建议 | 本地计算 | 逐条结论”三个分区，各自滚动、切换时保持位置。 */
export type Zone = 'ai' | 'local' | 'req'
const props = defineProps<{
  taskId: string
  run: RunDetail | null
  runLoading: boolean
  runError: string
  localOnly: boolean
  findings: Finding[]
  selectedId: string
  askEnabled: boolean
  localLoader: () => Promise<{ items: LocalFinding[] }>
  fresh: boolean
}>()
const severity = defineModel<string>('severity', { required: true })
const category = defineModel<string>('category', { required: true })
const handling = defineModel<string>('handling', { required: true })
const zone = defineModel<Zone>('zone', { required: true })
const emit = defineEmits<{
  select: [f: Finding]
  localSelect: [locs: string[]]
  updated: [f: Finding]
  ask: [prefill: string]
  aiNotice: []
}>()

const fx = useFx()
const categoryOptions = toOptions(CATEGORY_OPTIONS)
const handlingOptions = toOptions(HANDLING_OPTIONS)
const localCount = ref<number | null>(null)
const zoneOptions = computed(() => [
  { value: 'ai' as const, label: `AI 建议 ${props.run?.findings.length ?? 0}` },
  { value: 'local' as const, label: `本地计算 ${localCount.value ?? '—'}` },
  { value: 'req' as const, label: '逐条结论' },
])
const no = (f: Finding) => String((props.run?.findings.findIndex(item => item.id === f.id) ?? -1) + 1).padStart(2, '0')

// 刚完成的轮次第一次打开：完整特效下卡片依次上浮（每张 30ms，最多 12 张）
const stagger = ref(false)
onMounted(() => {
  if (!props.fresh || fx.value !== 'full') return
  stagger.value = true
  setTimeout(() => { stagger.value = false }, 1200)
})

const aiEl = ref<HTMLElement>()
const localEl = ref<HTMLElement>()
const reqEl = ref<HTMLElement>()
// 切换轮次时各分区回到顶部
watch(() => props.run?.id, () => { for (const el of [aiEl.value, localEl.value, reqEl.value]) if (el) el.scrollTop = 0 })
// 画布点了标注：问题栏滚到该卡片
watch(() => props.selectedId, async (id) => {
  if (!id) return
  await nextTick()
  aiEl.value?.querySelector<HTMLElement>(`[data-finding="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: fx.value === 'off' ? 'auto' : 'smooth' })
})
</script>

<template>
  <aside class="rail">
    <el-alert v-if="runError" type="error" show-icon :closable="false" :title="runError" class="alert" />
    <template v-if="run">
      <el-alert v-if="run.status === 'failed'" type="error" show-icon :closable="false" :title="run.error_message || '审核失败'" class="alert" />
      <el-alert v-else-if="run.status === 'partial' && run.error_message" type="warning" show-icon :closable="false" :title="run.error_message" class="alert" />
    </template>
    <div v-if="runLoading" class="loading"><el-skeleton :rows="6" animated /></div>
    <template v-else>
      <RailSummary v-model:severity="severity" :run="run" :local-only="localOnly" @ai-notice="emit('aiNotice')" />
      <div v-if="!localOnly && run" class="zone-switch"><SiSegmented v-model="zone" :options="zoneOptions" ariaLabel="问题分区" /></div>

      <div v-show="!localOnly && zone === 'ai'" ref="aiEl" class="zone">
        <template v-if="run">
          <ZoneHeader kind="ai" :count="findings.length">
            <el-select v-model="category" clearable placeholder="类别" size="small" class="filter"><el-option v-for="o in categoryOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
            <el-select v-model="handling" clearable placeholder="处理状态" size="small" class="filter"><el-option v-for="o in handlingOptions" :key="o.value" :label="o.label" :value="o.value" /></el-select>
          </ZoneHeader>
          <TransitionGroup name="si-list" tag="div" class="cards" :appear="stagger">
            <FindingCard v-for="(f, i) in findings" :key="f.id" :data-finding="f.id" :finding="f" :no="no(f)" :selected="f.id === selectedId" :ask-enabled="askEnabled"
              :style="stagger && i < 12 ? { transitionDelay: `${i * 30}ms` } : undefined"
              @select="emit('select', $event)" @updated="emit('updated', $event)" @ask="emit('ask', $event)" />
          </TransitionGroup>
          <EmptyState v-if="!findings.length" :icon="ListChecks"
            :title="run.findings.length ? '没有符合筛选条件的问题' : '本次没有列出问题条目'"
            :description="run.findings.length ? '调整上方的等级、类别或处理状态筛选后再看。' : '结果由 AI 生成，仅供参考，请以人工复核为准。'" />
        </template>
      </div>
      <div v-show="localOnly || zone === 'local'" ref="localEl" class="zone">
        <LocalCheckList :loader="localLoader" :load-key="taskId" @count="localCount = $event" @select="emit('localSelect', $event)" />
      </div>
      <div v-show="!localOnly && zone === 'req'" ref="reqEl" class="zone">
        <template v-if="run">
          <ZoneHeader kind="ai" :count="run.requirement_results.length" />
          <RequirementList :items="run.requirement_results" :ask-enabled="askEnabled" @ask="emit('ask', $event)" />
        </template>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.rail { display: flex; flex-direction: column; min-height: 0; height: 100%; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); overflow: hidden; }
.alert { flex: none; margin: 12px 16px 0; width: auto; }
.loading { padding: 16px; }
.zone-switch { flex: none; padding: 10px 16px 0; }
.zone-switch :deep(.si-segmented) { display: flex; width: 100%; }
.zone-switch :deep(.si-segmented button) { flex: 1; }
.zone { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 16px 16px; }
.filter { width: 104px; }
.cards { position: relative; display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
</style>
