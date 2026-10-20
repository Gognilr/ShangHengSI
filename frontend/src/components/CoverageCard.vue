<script setup lang="ts">
import type { RunDetail } from '@/api/runs'
import { sanctionsLabel } from '@/utils/labels'

defineProps<{ coverage: RunDetail['coverage']; sanctions: RunDetail['sanctions'] }>()
</script>

<template>
  <el-card shadow="never">
    <template v-if="coverage">
      <p class="summary">{{ coverage.summary }}</p>
      <ul v-if="coverage.notes.length">
        <li v-for="(note, i) in coverage.notes" :key="i">{{ note }}</li>
      </ul>
    </template>
    <div class="sanctions">
      <b>制裁结论：</b><span :class="{ high: sanctions.level === 'high' }">{{ sanctionsLabel(sanctions.level) }}</span>
      <ul v-if="sanctions.reasons.length">
        <li v-for="(reason, i) in sanctions.reasons" :key="i">{{ reason }}</li>
      </ul>
      <div class="hint">未联网核查，基于模型自身知识，需合规人员确认最新制裁与出口管制规定</div>
    </div>
  </el-card>
</template>

<style scoped>
.summary {
  margin: 0 0 8px;
}
ul {
  margin: 8px 0;
  padding-left: 20px;
}
li, .sanctions, .summary {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.high {
  color: var(--el-color-danger);
}
</style>
