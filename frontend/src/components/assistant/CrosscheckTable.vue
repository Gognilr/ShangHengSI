<script setup lang="ts">
import type { Crosscheck } from '@/api/assistant'
defineProps<{ result: Crosscheck }>()
// 状态只用圆点 + 文字：不一致为红，写法不同、文件内多值、无法比较为赭黄，其余中性（V34-A-08）
const TONE: Record<string, string> = { 不一致: 'danger', 写法不同: 'warning', 文件内有多个值: 'warning', 无法比较: 'warning' }
</script>
<template>
  <div class="crosscheck">
    <p class="hint">{{ result.notice }}<template v-if="result.rows.some(r => Object.values(r.values).some(vs => vs.some(v => v.by === 'model')))">；标 * 的值由模型在文件中定位，仍由本机比较</template></p>
    <p v-if="!result.rows.length" class="hint">所选文件中没有识别到可比较的项目（品名、数量、重量、件数、金额、收发货人、港口、日期、单号）</p>
    <div v-else class="table-scroll"><table>
      <thead><tr><th>项目</th><th v-for="file in result.files" :key="file.file">文件 {{ file.file }}<span class="hint name">{{ file.name }}</span></th><th>结论</th></tr></thead>
      <tbody><tr v-for="row in result.rows" :key="row.field">
        <th>{{ row.label }}</th>
        <td v-for="file in result.files" :key="file.file">
          <div v-for="(value, i) in row.values[String(file.file)] ?? []" :key="i" :title="value.place">{{ value.text }}<span v-if="value.by === 'model'" class="hint"> *</span></div>
          <span v-if="!(row.values[String(file.file)] ?? []).length" class="hint">未找到</span>
        </td>
        <td><span class="status" :class="`tone-${TONE[row.status] ?? 'plain'}`"><i class="dot" />{{ row.status }}</span></td>
      </tr></tbody>
    </table></div>
    <template v-if="result.findings.length">
      <p class="heading">其他跨文件提示</p>
      <p v-for="(text, i) in result.findings" :key="i" class="finding">{{ text }}</p>
    </template>
    <p v-for="(note, i) in result.notes" :key="'n' + i" class="hint">{{ note }}</p>
  </div>
</template>
<style scoped>
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font: var(--si-font-body); }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--el-border-color-light); text-align: left; vertical-align: top; min-width: 96px; overflow-wrap: anywhere; }
thead th { font-weight: 600; color: var(--si-ink-2); }
tbody th { font-weight: 500; white-space: nowrap; }
.name { display: block; font-weight: 400; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { display: inline-flex; align-items: center; gap: 6px; color: var(--si-ink-2); white-space: nowrap; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--si-ink-4); }
.tone-danger { color: var(--si-danger-ink); }
.tone-danger .dot { background: var(--si-danger-ink); }
.tone-warning .dot { background: var(--si-warn-ink); }
.heading { font-weight: 600; margin: 16px 0 4px; }
.finding { margin: 4px 0; }
</style>
