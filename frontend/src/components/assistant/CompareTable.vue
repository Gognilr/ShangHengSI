<script setup lang="ts">
import type { Comparison } from '@/api/assistant'
defineProps<{ comparison: Comparison }>()
</script>
<template>
  <div class="comparison">
    <p class="hint">价格由本地从文件中还原计算，未发送给模型</p>
    <div class="table-scroll"><table>
      <thead><tr><th>项目</th><th v-for="supplier in comparison.suppliers" :key="supplier.file">{{ supplier.name }}</th></tr></thead>
      <tbody><tr v-for="(row, index) in comparison.rows" :key="index"><th>{{ row.item }}<span class="hint">{{ row.unit }}</span></th>
        <td v-for="supplier in comparison.suppliers" :key="supplier.file" :class="{ minimum: row.minimum_files.includes(supplier.file) }">
          <template v-for="quote in row.quotes.filter(q => q.file === supplier.file)" :key="quote.file">
            <div class="num">单价 {{ quote.currency }} {{ quote.unit_price ?? '无法核对' }}</div><div class="num">金额 {{ quote.amount ?? '无法核对' }}</div>
            <div v-if="quote.difference_pct !== null" class="num">差额 {{ quote.difference_pct }}%</div>
            <div v-if="row.status !== '可核对'" class="warning">{{ row.status }}</div>
          </template>
          <span v-if="!row.quotes.some(q => q.file === supplier.file)" class="warning">无法核对</span>
        </td></tr>
        <tr><th>合计</th><td v-for="supplier in comparison.suppliers" :key="supplier.file" class="num">{{ comparison.totals[supplier.file]?.currency }} {{ comparison.totals[supplier.file]?.amount ?? '不完整' }}</td></tr>
      </tbody>
    </table></div>
    <p v-for="supplier in comparison.suppliers.filter(s => s.terms)" :key="supplier.file">{{ supplier.name }}：{{ supplier.terms }}</p>
    <p>{{ comparison.notes }}</p>
  </div>
</template>
<style scoped>
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font: var(--si-font-body); }
th, td { padding: 12px; border-bottom: 1px solid var(--el-border-color-light); text-align: left; vertical-align: top; min-width: 120px; overflow-wrap: anywhere; }
th { background: var(--el-fill-color-light); font-weight: 600; }
.minimum { font-weight: 600; }
.warning { color: var(--el-color-warning); }
.hint { display: block; }
</style>
