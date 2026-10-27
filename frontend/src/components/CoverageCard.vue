<script setup lang="ts">
import type { RunDetail } from '@/api/runs'
import { sanctionsLabel } from '@/utils/labels'

defineProps<{ coverage: RunDetail['coverage']; sanctions: RunDetail['sanctions']; docSummary?: string | null }>()
</script>

<template>
  <div class="coverage">
    <template v-if="coverage">
      <p class="summary">{{ coverage.summary }}</p>
    </template>
    <div class="sanctions">
      <b>制裁结论：</b><span :class="{ high: sanctions.level === 'high' }">{{ sanctionsLabel(sanctions.level) }}</span>
      <ul v-if="sanctions.reasons.length">
        <li v-for="(reason, i) in sanctions.reasons" :key="i">{{ reason }}</li>
      </ul>
      <div class="hint">未联网核查，基于模型自身知识，需合规人员确认最新制裁与出口管制规定</div>
    </div>
    <el-collapse v-if="docSummary || coverage?.notes.length">
      <el-collapse-item :title="`文件摘要与限制事项（${coverage?.notes.length ?? 0} 条）`" name="details">
        <p v-if="docSummary" class="document-summary">{{ docSummary }}</p>
        <ul v-if="coverage?.notes.length"><li v-for="(note, i) in coverage.notes" :key="i">{{ note }}</li></ul>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.coverage { margin-top: 12px; }
.document-summary { margin: 0 0 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
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
