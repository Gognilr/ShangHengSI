<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useFx } from '@/utils/motion'
import { money } from './money'

/**
 * 金额从 0 滚到终值（澄明二版第五节“扫票”）：只在完整档、只在第一次出现时滚一次；滚动中的数字只用于显示，
 * 终值直接用后端给的两位小数字符串（Decimal 计算在后端），精简与关闭档直接显示终值。
 */
const props = withDefaults(defineProps<{ value: string | null; roll?: boolean; delay?: number }>(), { roll: false, delay: 0 })
const fx = useFx()

const shown = ref(money(props.value))
let raf = 0, timer: ReturnType<typeof setTimeout> | undefined
onMounted(() => {
  if (!props.roll || fx.value !== 'full' || props.value == null) return
  const end = Number(props.value)
  if (!Number.isFinite(end) || end === 0) return
  const fmt = (x: number) => money((Math.round(x * 100) / 100).toFixed(2))
  shown.value = fmt(0)
  timer = setTimeout(() => {
    const t0 = performance.now(), D = 900
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - k, 3)
      shown.value = k < 1 ? fmt(end * e) : money(props.value)
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
  }, props.delay)
})
onBeforeUnmount(() => { cancelAnimationFrame(raf); clearTimeout(timer) })
</script>

<template>
  <span class="money" :aria-label="money(value)"><span aria-hidden="true">{{ shown }}</span></span>
</template>

<style scoped>
.money { font-family: var(--si-font-mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
