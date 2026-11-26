<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Check, X } from '@lucide/vue'
import { useFx } from '@/utils/motion'
import { barStations, currentName, isLocalTrip, miniStations, segmentsOf, tripAria, tripTitle, type Station, type Trip } from '@/utils/trip'

/**
 * 往返线「衡线」（澄明二版第二节）：一件工作从本机出发，遮住后经网关到模型，再回到本机还原。
 * 颜色说在哪里（本机青、模型紫），光圈说怎么样（呼吸进行中、琥珀等你、红叉失败）。
 * scale：mini 迷你（列表行，五个 8px 点）；bar 工具条（工作区顶部，模块站名横排）；dot 只画当前站（侧栏）。
 * 状态映射只在 utils/trip.ts。
 */
const props = withDefaults(defineProps<{ trip: Trip; scale?: 'mini' | 'bar' | 'dot'; elapsed?: string; took?: string; hideText?: boolean }>(), { scale: 'mini' })
const emit = defineEmits<{ act: [] }>()
const fx = useFx()

const label = computed(() => tripAria(props.trip))
const local = computed(() => isLocalTrip(props.trip))
const done = computed(() => props.trip.state === 'done')

/* ---- 迷你：8px 点、2px 线，五站宽 80 ---- */
const STEP = 18
const mini = computed(() => miniStations(props.trip))
function geom(ss: Station[]) {
  const segs = segmentsOf(ss)
  return {
    w: 8 + (ss.length - 1) * STEP,
    dots: ss.map((s, i) => ({ ...s, cx: 4 + i * STEP })),
    segs: segs.map((g, i) => {
      const x1 = 4 + i * STEP + 5, x2 = 4 + (i + 1) * STEP - 5
      return { ...g, x1, x2, xm: (x1 + x2) / 2 }
    }),
  }
}
const g = computed(() => geom(mini.value))
const curDot = computed(() => mini.value.find(s => s.state !== 'done' && s.state !== 'todo') ?? mini.value[mini.value.length - 1])
const miniText = computed(() => done.value ? '' : currentName(props.trip))

/* ---- 工具条 ---- */
const bar = computed(() => barStations(props.trip))
const barSegs = computed(() => segmentsOf(bar.value))
const all = computed(() => geom(miniStations({ ...props.trip, state: 'done' })))

/* ---- 走线：状态变化时光点沿线走一次，过复检后由青变紫，回到还原时变回青（只在完整档） ---- */
const ol = ref<HTMLOListElement>()
const spark = ref<HTMLElement>()
watch(() => [props.trip.mod, props.trip.at] as const, async ([mod, at], old) => {
  if (props.scale !== 'bar' || fx.value !== 'full' || !old || old[0] !== mod || at <= old[1]) return
  const from = old[1]
  await nextTick()
  const list = ol.value, sp = spark.value
  if (!list || !sp) return
  const dots = list.querySelectorAll<HTMLElement>('.ts .d')
  if (!dots[from] || !dots[at]) return
  const o = list.getBoundingClientRect()
  const x = (el: HTMLElement) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2 - o.left }
  const toModel = bar.value[at]?.zone === 'm', fromModel = bar.value[from]?.zone === 'm'
  sp.animate([{ transform: `translateX(${x(dots[from])}px)`, opacity: 1 }, { transform: `translateX(${x(dots[at])}px)`, opacity: 1, offset: .85 }, { transform: `translateX(${x(dots[at])}px)`, opacity: 0 }],
    { duration: 900, easing: 'cubic-bezier(.22,1,.36,1)' })
  sp.querySelector('.sm')?.animate([{ opacity: fromModel ? 1 : 0 }, { opacity: toModel ? 1 : 0 }], { duration: 900, fill: 'forwards' })
})
</script>

<template>
  <!-- 只画当前站：侧栏近期会话 -->
  <span v-if="scale === 'dot'" class="si-trip-dot" role="img" :aria-label="label" :title="tripTitle(trip)">
    <svg viewBox="-3 -1 14 14" aria-hidden="true">
      <circle v-if="curDot.state === 'run'" class="halo run" :class="curDot.zone" cx="4" cy="6" r="5" />
      <circle v-if="curDot.state === 'wait' || curDot.partial" class="halo w" cx="4" cy="6" r="6.5" />
      <circle class="dot" :class="[curDot.state === 'run' ? 'cur' : curDot.state, curDot.zone]" cx="4" cy="6" :r="curDot.state === 'fail' ? 5 : 4" />
      <path v-if="curDot.state === 'fail'" class="x" d="M2 4l4 4M6 4l-4 4" />
    </svg>
  </span>

  <!-- 迷你：列表行 -->
  <span v-else-if="scale === 'mini'" class="si-trip-mini" :class="[`st-${trip.state}`, { n3: local }]" role="img" :aria-label="label" :title="tripTitle(trip)">
    <svg :viewBox="`-2 -2 ${g.w + 4} 16`" aria-hidden="true">
      <template v-for="(s, i) in g.segs" :key="'s' + i">
        <template v-if="s.pass && s.zone === 'r'">
          <path class="seg m" :d="`M${s.x1} 6H${s.xm}`" /><path class="seg l" :d="`M${s.xm} 6H${s.x2}`" />
        </template>
        <path v-else class="seg" :class="s.pass ? s.zone : ''" :d="`M${s.x1} 6H${s.x2}`" />
      </template>
      <template v-for="(d, i) in g.dots" :key="'d' + i">
        <circle v-if="d.state === 'run'" class="halo run" :class="d.zone" :cx="d.cx" cy="6" r="5" />
        <circle v-if="d.state === 'wait' || d.partial" class="halo w" :cx="d.cx" cy="6" r="6.5" />
        <circle class="dot" :class="[d.state === 'run' ? 'cur' : d.state, d.zone]" :cx="d.cx" cy="6" :r="d.state === 'fail' ? 5 : 4" />
        <path v-if="d.state === 'fail'" class="x" :d="`M${d.cx - 2} 4l4 4M${d.cx + 2} 4l-4 4`" />
      </template>
    </svg>
    <span v-if="!hideText" class="t"><b v-if="miniText">{{ miniText }}</b><template v-if="trip.note">{{ miniText ? ' · ' : '' }}{{ trip.note }}</template><template v-else-if="done">已完成{{ local ? ' · 全程本机' : '' }}</template></span>
  </span>

  <!-- 工具条：完成后收成徽标 -->
  <span v-else-if="done" class="si-trip-done" role="status">
    <svg class="tdl" :viewBox="`-2 -2 ${all.w + 4} 16`" aria-hidden="true">
      <template v-for="(s, i) in all.segs" :key="'s' + i">
        <template v-if="s.zone === 'r'"><path class="seg m" :d="`M${s.x1} 6H${s.xm}`" /><path class="seg l" :d="`M${s.xm} 6H${s.x2}`" /></template>
        <path v-else class="seg" :class="s.zone" :d="`M${s.x1} 6H${s.x2}`" />
      </template>
      <circle v-for="(d, i) in all.dots" :key="'d' + i" class="dot done" :class="d.zone" :cx="d.cx" cy="6" r="4" />
    </svg>
    <Check :size="14" :stroke-width="2.25" aria-hidden="true" />已完成<template v-if="took"> · 用时 {{ took }}</template><template v-if="local"> · 全程本机</template>
  </span>
  <ol v-else ref="ol" class="si-trip-bar" :aria-label="label">
    <li v-for="(s, i) in bar" :key="i" class="ts" :class="[s.zone, s.state === 'run' ? 'cur' : s.state, { partial: s.partial, now: i === trip.at && s.state !== 'todo', [`pass-${barSegs[i - 1]?.zone}`]: i > 0 && barSegs[i - 1]?.pass }]" :aria-current="i === trip.at ? 'step' : undefined">
      <span class="d" aria-hidden="true"><X v-if="s.state === 'fail'" :size="10" :stroke-width="3" /></span>{{ s.name }}
      <span v-if="i === trip.at && s.state === 'run' && elapsed" class="tm">{{ elapsed }}</span>
      <span v-if="i === trip.at && trip.note && s.state !== 'todo'" class="note" :class="s.state">{{ trip.note }}
        <el-button v-if="trip.act && ['wait', 'block', 'fail'].includes(s.state)" size="small" :type="s.state === 'fail' ? 'default' : 'primary'" @click="emit('act')">{{ trip.act }}</el-button>
      </span>
    </li>
    <li ref="spark" class="spark" aria-hidden="true"><i class="sl" /><i class="sm" /></li>
  </ol>
</template>

<style scoped>
/* ---- 迷你与当前站 ---- */
.si-trip-mini { display: inline-flex; align-items: center; gap: 8px; min-width: 0; vertical-align: middle; }
.si-trip-mini svg { flex: none; width: 80px; height: 12px; overflow: visible; }
.si-trip-mini.n3 svg { width: 44px; }
.si-trip-mini .t { min-width: 0; overflow: hidden; color: var(--si-ink-3); font: var(--si-font-meta); white-space: nowrap; text-overflow: ellipsis; }
.si-trip-mini .t b { font-weight: 500; color: var(--si-ink-2); }
.si-trip-mini:is(.st-wait, .st-block) .t b { color: var(--si-warn-ink); }
.si-trip-mini.st-fail .t b { color: var(--si-danger-ink); }
.si-trip-dot { display: inline-flex; flex: none; }
.si-trip-dot svg { width: 14px; height: 14px; overflow: visible; }
.seg { fill: none; stroke: var(--si-hair-strong); stroke-width: 2; stroke-linecap: round; }
.seg.l { stroke: var(--si-trip-l); }
.seg.m { stroke: var(--si-trip-m); }
.dot { fill: var(--si-surface-1); stroke: var(--si-trip-idle); stroke-width: 1.5; }
.dot:is(.done, .cur).l { fill: var(--si-trip-l); stroke: var(--si-trip-l); }
.dot:is(.done, .cur).m { fill: var(--si-trip-m); stroke: var(--si-trip-m); }
.dot.wait { fill: var(--si-surface-1); stroke: var(--si-warn); stroke-width: 2; }
.dot.block { fill: var(--si-warn); stroke: var(--si-warn); }
.dot.fail { fill: var(--si-danger); stroke: var(--si-danger); }
.dot.stop { fill: var(--si-ink-4); stroke: var(--si-ink-4); }
.x { stroke: var(--si-white); stroke-width: 1.4; stroke-linecap: round; }
.halo { fill: none; stroke-width: 2; opacity: .5; transform-box: fill-box; transform-origin: center; }
.halo.l { stroke: var(--si-trip-l); }
.halo.m { stroke: var(--si-trip-m); }
.halo.w { stroke: var(--si-warn); }
.halo.run { animation: trip-breath 1.8s var(--si-ease-io) infinite; }
@keyframes trip-breath { 0% { transform: scale(.7); opacity: .7; } 70%, 100% { transform: scale(1.7); opacity: 0; } }
html:is([data-fx="lite"], [data-fx="off"]) .halo.run { animation: none; opacity: .45; transform: scale(1.25); }

/* ---- 工具条 ---- */
.si-trip-bar { position: relative; display: flex; align-items: center; min-width: 0; margin: 0; padding: 0; list-style: none; }
.ts { position: relative; display: flex; align-items: center; gap: 8px; color: var(--si-ink-3); font: var(--si-font-label-sm); white-space: nowrap; }
.ts + .ts::before { content: ""; width: 28px; height: 2px; margin: 0 10px 0 2px; border-radius: 2px; background: var(--si-hair-strong); }
.ts.pass-l::before { background: var(--si-trip-l); }
.ts.pass-m::before { background: var(--si-trip-m); }
.ts.pass-r::before { background: linear-gradient(90deg, var(--si-trip-m), var(--si-trip-l)); }
.ts.l { --zc: var(--si-trip-l); }
.ts.m { --zc: var(--si-trip-m); }
.d { position: relative; flex: none; display: grid; place-items: center; width: 10px; height: 10px; border: 1.5px solid var(--si-trip-idle); border-radius: 50%; box-sizing: border-box; background: var(--si-surface-1); color: var(--si-white); }
.ts.done { color: var(--si-ink-2); }
.ts.done .d, .ts.cur .d { background: var(--zc); border-color: var(--zc); }
.ts.now { color: var(--si-ink-1); font-weight: 600; }
.ts.cur .d::after { content: ""; position: absolute; inset: -5px; border: 2px solid var(--zc); border-radius: 50%; opacity: .5; animation: trip-ring 1.8s var(--si-ease-io) infinite; }
@keyframes trip-ring { 0% { transform: scale(.7); opacity: .7; } 70%, 100% { transform: scale(1.4); opacity: 0; } }
html:is([data-fx="lite"], [data-fx="off"]) .ts.cur .d::after { inset: -4px; animation: none; opacity: .4; }
.ts.wait .d { background: var(--si-surface-1); border: 2px solid var(--si-warn); }
.ts:is(.wait, .block) .d::after { content: ""; position: absolute; inset: -4px; border: 2px solid var(--si-warn); border-radius: 50%; opacity: .45; }
.ts.block .d { background: var(--si-warn); border-color: var(--si-warn); }
.ts:is(.fail, .stop) .d { width: 14px; height: 14px; }
.ts.fail .d { background: var(--si-danger); border-color: var(--si-danger); }
.ts.stop .d { background: var(--si-ink-4); border-color: var(--si-ink-4); }
.ts.partial .d::after { content: ""; position: absolute; inset: -4px; border: 2px solid var(--si-warn); border-radius: 50%; opacity: .55; }
.tm { font: var(--si-font-code-sm); font-weight: 400; color: var(--si-ink-3); }
.note { display: inline-flex; align-items: center; gap: 8px; margin-left: 4px; font: var(--si-font-meta); font-weight: 400; color: var(--si-ink-2); white-space: nowrap; }
.note:is(.wait, .block) { color: var(--si-warn-ink); }
.note.fail { color: var(--si-danger-ink); }
.spark { position: absolute; left: 0; top: 50%; width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%; pointer-events: none; opacity: 0; }
.spark i { position: absolute; inset: 0; border-radius: 50%; }
.spark .sl { background: var(--si-trip-l); box-shadow: 0 0 0 3px color-mix(in srgb, var(--si-trip-l) 30%, transparent); }
.spark .sm { background: var(--si-trip-m); box-shadow: 0 0 0 3px color-mix(in srgb, var(--si-trip-m) 30%, transparent); opacity: 0; }

/* ---- 完成徽标 ---- */
.si-trip-done { display: inline-flex; align-items: center; gap: 8px; height: 30px; padding: 0 12px 0 8px; border-radius: var(--si-radius-badge); background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-label-sm); white-space: nowrap; animation: trip-pop var(--si-d3) var(--si-ease-spring) both; }
.si-trip-done .tdl { width: 44px; height: 10px; overflow: visible; }
@keyframes trip-pop { from { opacity: 0; transform: scale(.9); } to { opacity: 1; transform: none; } }
</style>
