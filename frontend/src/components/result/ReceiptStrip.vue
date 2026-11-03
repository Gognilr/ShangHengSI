<script setup lang="ts">
import { computed, ref } from 'vue'
import { ShieldCheck } from '@lucide/vue'
import type { RunDetail } from '@/api/runs'
import SentDrawer from '@/components/result/SentDrawer.vue'
import { NOT_SENT, redactionCategoryLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

/** 结果页工具条上的外发回执（V31-U3 3.2）：只显示接口给的数字；receipt 为 null 时不渲染。 */
const props = defineProps<{ run: Pick<RunDetail, 'id' | 'receipt' | 'restore_stats'>; fileNames: string[] }>()
const sentOpen = ref(false)
const popOpen = ref(false)

const receipt = computed(() => props.run.receipt)
const restored = computed(() => props.run.restore_stats.restored ?? 0)
const marked = computed(() => props.run.restore_stats.marked ?? 0)
const categories = computed(() => Object.entries(receipt.value?.protected ?? {}).filter(([, n]) => n > 0))
const notSent = computed(() => {
  const parts = [`原件 · ${props.fileNames.join('、')}`]
  for (const [key, n] of Object.entries(receipt.value?.not_sent ?? {})) if (n > 0) parts.push(`${NOT_SENT[key] ?? key} ${n}`)
  return parts.join(' · ')
})

function openSent() {
  popOpen.value = false
  sentOpen.value = true
}
</script>

<template>
  <template v-if="receipt">
    <el-popover v-model:visible="popOpen" trigger="click" placement="bottom-start" :width="360" popper-class="receipt-pop">
      <template #reference>
        <button type="button" class="strip" aria-haspopup="dialog">
          <ShieldCheck class="icon" :size="14" :stroke-width="1.75" aria-hidden="true" />
          <span>已保护 {{ receipt.protected_total }}</span><span class="dot recheck">·</span>
          <span class="recheck">复检 {{ receipt.rechecks }} 次 · 拦截 {{ receipt.blocked }}</span>
          <!-- V31Q-87：只写计数。比例会把“模型只提到一个文件中没有的代号”显示成“还原 0%”，像是还原失败 -->
          <template v-if="restored"><span class="dot">·</span><span>还原 {{ restored }}</span></template>
          <template v-if="marked"><span class="dot">·</span><span class="marked">标出 {{ marked }}</span></template>
        </button>
      </template>
      <dl class="rows">
        <dt>发送</dt>
        <dd>发送给 {{ receipt.model ?? '—' }} · {{ formatDateTime(receipt.sent_at) }} · {{ receipt.calls }} 次调用 · 共 {{ receipt.chars }} 字</dd>
        <dt>已在本机保护</dt>
        <dd>
          {{ receipt.protected_total }} 处
          <span class="chips"><span v-for="[cat, n] in categories" :key="cat" class="chip">{{ redactionCategoryLabel(cat) }} {{ n }}</span></span>
        </dd>
        <template v-if="receipt.kept > 0"><dt>不确定项</dt><dd>保留原文 {{ receipt.kept }} 组</dd></template>
        <template v-if="receipt.date_shifted || receipt.ratios_sent">
          <dt>处理</dt>
          <dd>{{ [receipt.date_shifted && '日期已整体平移', receipt.ratios_sent && '附带本地财务比率'].filter(Boolean).join(' · ') }}</dd>
        </template>
        <dt>不发送</dt>
        <dd>{{ notSent }}</dd>
        <dt>出口复检</dt>
        <dd>{{ receipt.rechecks }} 次 · 拦截 {{ receipt.blocked }} 次</dd>
        <dt>本机还原</dt>
        <dd v-if="restored || marked">已还原 {{ restored }} 处<template v-if="marked > 0">；标出 {{ marked }} 处模型写法无法对应到原文，见虚线标记</template></dd>
        <dd v-else>模型回复中没有代号，无需还原</dd>
      </dl>
      <el-button link type="primary" class="sent-link" @click="openSent">查看本轮实际发送的文字</el-button>
    </el-popover>
    <SentDrawer v-model="sentOpen" :run-id="run.id" />
  </template>
</template>

<style scoped>
.strip { display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px; border: 1px solid var(--si-hair); border-radius: var(--el-border-radius-round); background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; cursor: pointer; }
.strip:hover { background: var(--si-surface-2); }
.strip:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.icon { color: var(--el-color-primary); }
.dot { color: var(--si-ink-3); }
.marked { color: var(--si-warn-ink); }
/* 工具条较窄（1440 宽及以下、侧栏展开）时只留保护数与还原、标出计数，复检与拦截在弹层中 */
@container result-toolbar (max-width: 1200px) { .recheck { display: none; } }
.rows { display: grid; grid-template-columns: 84px 1fr; gap: 6px 10px; margin: 0; font: var(--si-font-meta); }
.rows dt { color: var(--si-ink-3); }
.rows dd { margin: 0; color: var(--el-text-color-primary); overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.chip { padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--si-surface-2); font: var(--si-font-label-sm); line-height: 20px; color: var(--si-ink-2); }
.sent-link { margin-top: 10px; padding: 0; }
</style>
