<script setup lang="ts">
import { computed } from 'vue'
import type { BidOpeningRuler } from '@/api/bid'

const props = defineProps<{ ruler: BidOpeningRuler; dates: [string, string] | null }>()
const emit = defineEmits<{ filter: [date: string | null] }>()
const days = computed(() => {
  const counts = new Map(props.ruler.days.map(day => [day.date, day.count]))
  const cursor = new Date(`${props.ruler.start}T00:00:00Z`)
  const end = Date.parse(`${props.ruler.end}T00:00:00Z`)
  const ticks = []
  while (cursor.getTime() <= end) {
    const date = cursor.toISOString().slice(0, 10)
    const today = date === props.ruler.today
    const count = counts.get(date) ?? 0
    const selected = props.dates?.[0] === date && props.dates[1] === date
    ticks.push({ date, count, today, selected, label: !today && cursor.getUTCDay() === 1 })
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
  return ticks
})
</script>

<template>
  <section class="ruler si-card" aria-label="开标时间尺">
    <header class="ruler-head">
      <h2>开标时间尺</h2>
      <span>近 30 天 · 点日期筛选项目</span>
      <el-button v-if="dates" link type="primary" @click="emit('filter', null)">清除日期筛选</el-button>
    </header>
    <div class="ticks" :style="{ '--days': days.length }">
      <div v-for="day in days" :key="day.date" class="tick" :class="{ today: day.today, major: day.label }">
        <time v-if="day.today" :datetime="day.date" class="today-label num">今天 {{ day.date.slice(5) }}</time>
        <button v-if="day.count" type="button" class="marker" :aria-label="`${day.date} 开标 · ${day.count} 个项目`"
          :title="`${day.date} · ${day.count} 个项目`" :aria-pressed="day.selected" @click="emit('filter', day.selected ? null : day.date)">
          <span class="num" aria-hidden="true">{{ day.count > 9 ? '9+' : day.count > 1 ? day.count : '' }}</span>
        </button>
        <time v-if="day.label" :datetime="day.date" class="tick-label num">{{ day.date.slice(5) }}</time>
      </div>
    </div>
    <p v-if="!ruler.days.length" class="ruler-empty">这段时间没有开标项目</p>
  </section>
</template>

<style scoped>
.ruler { padding: 14px 18px 12px; }
.ruler-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; color: var(--si-ink-3); font: var(--si-font-meta); }
.ruler-head h2 { margin: 0; color: var(--si-ink-1); font: var(--si-font-h3); }
.ruler-head .el-button { margin-left: auto; }
.ticks { position: relative; display: grid; grid-template-columns: repeat(var(--days), minmax(0, 1fr)); height: 66px; margin: 8px 8px 0; }
.ticks::before { content: ''; position: absolute; inset: auto 0 21px; height: 2px; background: var(--si-hair-strong); }
.tick { position: relative; display: flex; justify-content: center; }
.tick::before { content: ''; position: absolute; bottom: 21px; left: 50%; width: 1px; height: 6px; background: var(--si-hair-strong); }
.tick.major::before { height: 10px; }
.tick.today::before { top: 16px; bottom: 18px; height: auto; width: 2px; background: var(--si-trip-l); }
.today-label, .tick-label { position: absolute; white-space: nowrap; }
.today-label { top: 0; left: calc(50% + 5px); color: var(--si-blue-ink); font: var(--si-font-label-sm); }
.tick-label { bottom: 0; color: var(--si-ink-3); font: var(--si-font-code-sm); }
.marker { position: absolute; bottom: 26px; display: grid; place-items: center; width: 100%; max-width: 30px; height: 22px; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-1); cursor: pointer; }
.marker span { display: grid; place-items: center; min-width: 12px; min-height: 8px; padding: 0 2px; border-radius: 4px; background: var(--si-m-bid); color: var(--si-ink-1); font: var(--si-font-code-sm); }
.marker span:not(:empty) { background: var(--si-m-bid-soft); box-shadow: inset 0 0 0 1px var(--si-m-bid); }
.marker:hover span, .marker[aria-pressed="true"] span { box-shadow: 0 0 0 2px var(--si-surface-1), 0 0 0 4px var(--si-m-bid); }
.marker:focus-visible { outline: none; box-shadow: var(--si-ring); }
.ruler-empty { margin: 4px 0 0; color: var(--si-ink-3); font: var(--si-font-meta); }
</style>
