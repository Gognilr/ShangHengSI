<script setup lang="ts">
import { computed } from 'vue'
import type { BidOpeningRuler } from '@/api/bid'

// 开标时间尺（澄明二版 6.10）：近 30 天，每个项目在开标日上叠一格，有严重问题的格为红，已过的日期变淡；今天一道竖线。
const props = defineProps<{ ruler: BidOpeningRuler; dates: [string, string] | null }>()
const emit = defineEmits<{ filter: [date: string | null] }>()
const STACK = 3
const days = computed(() => {
  const marks = new Map(props.ruler.days.map(day => [day.date, day]))
  const cursor = new Date(`${props.ruler.start}T00:00:00Z`)
  const end = Date.parse(`${props.ruler.end}T00:00:00Z`)
  const ticks = []
  while (cursor.getTime() <= end) {
    const date = cursor.toISOString().slice(0, 10)
    const mark = marks.get(date)
    const count = mark?.count ?? 0, severe = mark?.severe ?? 0
    const today = date === props.ruler.today
    const selected = props.dates?.[0] === date && props.dates[1] === date
    const cells = Array.from({ length: Math.min(count, STACK) }, (_, i) => i < severe)
    const title = `${date.slice(5)} 开标 · ${count} 个项目${severe ? `，${severe} 个有严重问题` : ''}`
    ticks.push({ date, count, cells, more: count - cells.length, title, today, past: date < props.ruler.today, selected, label: !today && cursor.getUTCDay() === 1 })
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
  return ticks
})
</script>

<template>
  <section class="ruler si-card" aria-label="开标时间尺">
    <header class="ruler-head">
      <h2>开标时间尺</h2>
      <span>近 30 天 · 点日期筛到那天的项目</span>
      <el-button v-if="dates" link type="primary" @click="emit('filter', null)">显示全部</el-button>
    </header>
    <div class="ticks" :style="{ '--days': days.length }">
      <div v-for="day in days" :key="day.date" class="tick" :class="{ today: day.today, major: day.label }">
        <time v-if="day.today" :datetime="day.date" class="today-label num">今天 {{ day.date.slice(5) }}</time>
        <button v-if="day.count" type="button" class="marker" :class="{ past: day.past }" :aria-label="day.title" :title="day.title"
          :aria-pressed="day.selected" @click="emit('filter', day.selected ? null : day.date)">
          <i v-for="(bad, i) in day.cells" :key="i" :class="{ bad }" aria-hidden="true" />
          <span v-if="day.more > 0" class="more num" aria-hidden="true">+{{ day.more }}</span>
        </button>
        <time v-if="day.label" :datetime="day.date" class="tick-label num">{{ day.date.slice(5) }}</time>
      </div>
    </div>
    <p v-if="!ruler.days.length" class="ruler-empty">这段时间没有开标项目</p>
  </section>
</template>

<style scoped>
.ruler { padding: 16px 18px 12px; }
.ruler-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; color: var(--si-ink-3); font: var(--si-font-meta); }
.ruler-head h2 { margin: 0; color: var(--si-ink-1); font: var(--si-font-h3); }
.ruler-head .el-button { margin-left: auto; }
.ticks { position: relative; display: grid; grid-template-columns: repeat(var(--days), minmax(0, 1fr)); height: 76px; margin: 8px 8px 0; }
.ticks::before { content: ''; position: absolute; inset: auto 0 18px; height: 2px; background: var(--si-hair-strong); }
.tick { position: relative; display: flex; justify-content: center; }
.tick::before { content: ''; position: absolute; bottom: 18px; left: 50%; width: 1px; height: 6px; background: var(--si-hair-strong); }
.tick.major::before { height: 10px; }
.tick.today::before { top: 2px; bottom: 12px; height: auto; width: 2px; margin-left: -.5px; border-radius: 1px; background: var(--si-trip-l); }
.today-label, .tick-label { position: absolute; white-space: nowrap; }
.today-label { top: 0; left: calc(50% + 6px); color: var(--si-blue-ink); font: var(--si-font-label-sm); }
.tick-label { bottom: 0; color: var(--si-ink-4); font: var(--si-font-code-sm); }
/* 每个项目一格，自下而上叠放 */
.marker { position: absolute; bottom: 24px; z-index: 1; display: flex; flex-direction: column-reverse; align-items: center; gap: 3px; padding: 2px; border: 0; border-radius: var(--si-radius-sm); background: transparent; cursor: pointer; }
.marker i { display: block; width: 14px; height: 8px; border-radius: 3px; background: var(--si-m-bid); transition: transform var(--si-d2) var(--si-ease-out); }
.marker i.bad { background: var(--si-danger); }
.marker.past i { opacity: .45; }
.marker .more { color: var(--si-ink-3); font: var(--si-font-code-sm); }
html:not([data-fx="off"]) .marker:hover i { transform: scaleY(1.3); }
.marker[aria-pressed="true"] i { box-shadow: 0 0 0 2px var(--si-surface-1), 0 0 0 4px var(--si-m-bid); }
.marker:focus-visible { outline: none; box-shadow: var(--si-ring); }
.ruler-empty { margin: 4px 0 0; color: var(--si-ink-3); font: var(--si-font-meta); }
</style>
