<script setup lang="ts">
import type { RunDetail } from '@/api/runs'
import CoverageCard from '@/components/CoverageCard.vue'

defineProps<{ run: RunDetail | null; local: boolean; counts: { value: string; label: string; count: number }[]; severity: string }>()
const emit = defineEmits<{ select: [value: string] }>()
</script>

<template>
  <section class="panel summary">
    <p v-if="local">该任务未外发，仅做本地校验，没有 AI 审核结果。</p>
    <template v-else-if="run">
      <div class="heading">
        <h2>共 {{ run.findings.length }} 处问题</h2>
        <div class="counts" aria-label="按问题等级筛选">
          <button v-for="item in counts" :key="item.value" class="count" :class="[`severity-${item.value}`, { active: severity === item.value }]" :aria-pressed="severity === item.value" @click="emit('select', severity === item.value ? '' : item.value)"><i />{{ item.label }} <span class="num">{{ item.count }}</span></button>
        </div>
      </div>
      <CoverageCard :coverage="run.coverage" :sanctions="run.sanctions" :doc-summary="run.doc_summary" />
    </template>
  </section>
</template>

<style scoped>
.summary { padding: 20px 24px; }
.summary > p { margin: 0; }
.heading { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
h2 { margin: 0; font: var(--si-font-h2); }
.counts { display: flex; flex-wrap: wrap; gap: 8px; }
.count { display: flex; align-items: center; gap: 4px; padding: 4px 8px; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: var(--el-bg-color); color: var(--el-text-color-primary); font: var(--si-font-meta); cursor: pointer; }
.count.active { border-color: var(--el-color-primary); }
.count i { width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-info); }
.severity-high i { background: var(--el-color-danger); }
.severity-medium i { background: var(--el-color-warning); }
</style>
