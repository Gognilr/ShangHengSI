<script setup lang="ts" generic="T extends TimelineItem">
import { computed } from 'vue'
import SiBadge, { type BadgeTone } from '@/components/ui/SiBadge.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import type { Trip } from '@/utils/trip'
import { dayGroup, formatClock, formatDayLabel, formatShort, type DayGroup } from '@/utils/time'

/**
 * 按天的时间线（澄明二版 6.18）：左边日期（今天、昨天、本周、更早），右边一条竖线串起行卡，
 * 每行：名称加一句说明、迷你往返线、状态徽标、时间、行尾操作插槽。整行可点。
 * 文件审核、会议纪要、翻译的“最近的…”与历史记录共用；mod 决定竖线上圆点的模块色。
 */
export interface TimelineItem {
  key: string; title: string; sub?: string; trip: Trip; time: string
  mod: 'review' | 'bid' | 'meet' | 'trans' | 'chat'
  badge?: { text: string; tone: BadgeTone; dot?: boolean } | null
}
const props = defineProps<{ items: T[] }>()
const emit = defineEmits<{ open: [item: T] }>()
const NAME: Record<DayGroup, string> = { today: '今天', yesterday: '昨天', week: '本周', earlier: '更早' }
const days = computed(() => {
  const out: { group: DayGroup; label: string; sub: string; items: T[] }[] = []
  for (const it of props.items) {
    const g = dayGroup(it.time)
    const last = out[out.length - 1]
    if (last?.group === g) last.items.push(it)
    else out.push({ group: g, label: NAME[g], sub: formatDayLabel(it.time), items: [it] })
  }
  return out
})
const when = (g: DayGroup, t: string) => g === 'today' || g === 'yesterday' ? formatClock(t) : formatShort(t)
</script>

<template>
  <div class="tl">
    <div v-for="d in days" :key="d.group" class="tl-day">
      <div class="dl"><b>{{ d.label }}</b>{{ d.sub }}</div>
      <ol class="tl-items">
        <li v-for="(it, i) in d.items" :key="it.key" class="tl-it" :class="`m-${it.mod}`" :style="{ '--i': i }" tabindex="0" role="link"
          :aria-label="it.title" @click="emit('open', it)" @keydown.enter.self="emit('open', it)">
          <span class="ti-n"><b :title="it.title">{{ it.title }}</b><span v-if="it.sub">{{ it.sub }}</span></span>
          <SiTrip :trip="it.trip" class="ti-trip" />
          <SiBadge v-if="it.badge" :tone="it.badge.tone" :dot="it.badge.dot">{{ it.badge.text }}</SiBadge>
          <span class="ti-t num">{{ when(d.group, it.time) }}</span>
          <span v-if="$slots.actions" class="ti-a" @click.stop @keydown.stop><slot name="actions" :item="it" /></span>
        </li>
      </ol>
    </div>
  </div>
</template>

<style scoped>
.tl-day { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 0 18px; }
.tl-day + .tl-day { margin-top: 6px; }
.dl { padding-top: 16px; text-align: right; color: var(--si-ink-3); font: var(--si-font-meta); }
.dl b { display: block; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; }
.tl-items { position: relative; display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 6px 0 6px 22px; list-style: none; }
.tl-items::before { content: ''; position: absolute; left: 5px; top: 0; bottom: 0; width: 2px; border-radius: 1px; background: var(--si-hair); }
.tl-it { --mc: var(--si-m-review); position: relative; display: flex; align-items: center; gap: 14px; min-height: 60px; padding: 10px 14px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out); }
html[data-fx="full"] .tl-it { animation: tl-in var(--si-d3) var(--si-ease-out) both; animation-delay: calc(min(var(--i), 8) * 45ms + 120ms); }
@keyframes tl-in { from { opacity: 0; transform: translateY(8px); } }
.tl-it:hover { transform: translateX(3px); box-shadow: var(--si-shadow-2); }
.tl-it:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 2px; }
.tl-it::before { content: ''; position: absolute; left: -22px; top: 50%; width: 12px; height: 12px; margin-top: -6px; box-sizing: border-box; border: 3px solid var(--mc); border-radius: 50%; background: var(--si-surface-1); }
.m-bid { --mc: var(--si-m-bid); } .m-meet { --mc: var(--si-m-meet); } .m-trans { --mc: var(--si-m-trans); } .m-chat { --mc: var(--si-ai); }
.ti-n { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ti-n b { overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.ti-n span { overflow: hidden; color: var(--si-ink-3); font: var(--si-font-caption); text-overflow: ellipsis; white-space: nowrap; }
.ti-trip { flex: none; width: 216px; }
.ti-t { flex: none; width: 88px; color: var(--si-ink-3); font: var(--si-font-caption); text-align: right; white-space: nowrap; }
.ti-a { flex: none; display: flex; align-items: center; }
@media (max-width: 1100px) { .ti-trip { width: auto; } .tl-day { grid-template-columns: 76px minmax(0, 1fr); } }
</style>
