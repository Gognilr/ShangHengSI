<script setup lang="ts">
// 内置价格表匹配到的单价，只读（V351Q-21）：模型卡片与编辑抽屉共用
import { computed } from 'vue'
import type { ModelPrice, ModelPriceTier } from '@/api/admin'

const props = defineProps<{ price: ModelPrice | null }>()

function k(n: number) { return n % 1000 === 0 ? `${n / 1000}K` : String(n) }
function label(tiers: ModelPriceTier[], i: number) {
  const t = tiers[i]!, prev = tiers[i - 1]
  const parts: string[] = []
  if (t.max_input !== null) parts.push(`输入 ≤${k(t.max_input)}`)
  else if (prev?.max_input != null) parts.push(`输入 >${k(prev.max_input)}`)
  if (t.max_output !== null) parts.push(`输出 ≤${k(t.max_output)}`)
  else if (prev?.max_output != null && prev.max_input === t.max_input) parts.push(`输出 >${k(prev.max_output)}`)
  return parts.join('、')
}
function text(t: ModelPriceTier) {
  const thinking = t.output_thinking && t.output_thinking !== t.output ? `（思考 ${t.output_thinking}）` : ''
  return `输入 ${t.input} · 命中缓存 ${t.cached} · 输出 ${t.output}${thinking}`
}
const rows = computed(() => {
  const p = props.price
  if (!p) return []
  const peak = p.offpeak ? '高峰 ' : ''
  const list = p.tiers.map((t, i) => ({ label: peak + label(p.tiers, i), text: text(t) }))
  if (p.offpeak) list.push(...p.offpeak.map((t, i) => ({ label: '空闲 ' + label(p.offpeak!, i), text: text(t) })))
  return list.map(r => ({ ...r, label: r.label.trim() }))
})
const sourceHost = computed(() => { try { return props.price ? new URL(props.price.source).host : '' } catch { return '' } })
</script>

<template>
  <div class="price-info">
    <template v-if="price">
      <p class="unit">单价（元 / 百万 token）</p>
      <ul>
        <li v-for="(row, i) in rows" :key="i"><span v-if="row.label" class="tier">{{ row.label }}</span><span class="num">{{ row.text }}</span></li>
      </ul>
      <p v-if="price.peak_hours" class="hint">高峰为北京时间工作日 {{ price.peak_hours.join('、') }}，其余时段、周末与法定节假日为空闲。</p>
      <p class="hint">价格表 {{ price.version }} · 来源 <a :href="price.source" target="_blank" rel="noopener noreferrer">{{ sourceHost }}</a>{{ price.note ? ` · ${price.note}` : '' }}</p>
    </template>
    <p v-else class="hint">未收录价格，费用只统计 token。价格表按接口主机与模型名称匹配，随系统升级更新。</p>
  </div>
</template>

<style scoped>
.price-info { display: grid; gap: 4px; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
p, ul { margin: 0; }
ul { padding: 0; list-style: none; display: grid; gap: 2px; }
li { display: flex; flex-wrap: wrap; gap: 0 8px; color: var(--el-text-color-regular); }
.tier { color: var(--el-text-color-secondary); }
.unit { color: var(--el-text-color-secondary); }
</style>
