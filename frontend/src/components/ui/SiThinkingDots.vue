<script setup lang="ts">
import { ref } from 'vue'
import { useFx, useLoop } from '@/utils/motion'
// 对话等待的墨点聚散（V33-C-06）：8 个小点各沿自己的小轨道往返并呼吸，约 58×34px，稀疏克制。
// 组件存在即在等待（由所在处 v-if 控制）；完整：轨道循环（页面隐藏暂停）；精简：静态排布；关闭：不显示。
// 位置、直径、颜色、轨道半径与摆角、周期均写死，是状态指示，不是数据视图。
const fx = useFx(), playing = useLoop(ref(true))
const COLORS = ['--si-blue', '--si-cyan', '--si-indigo', '--si-ink-4']
// [x, y, 直径, 颜色, 半径, 起角, 终角, 轨道周期s, 呼吸周期s]
const DOTS: [number, number, number, number, number, number, number, number, number][] = [
  [10, 10, 5, 0, 7, -40, 140, 2.3, 1.9],
  [24, 22, 6, 1, 6, 20, 200, 3.1, 2.4],
  [38, 9, 4, 2, 8, 100, -80, 2.7, 2.1],
  [49, 24, 5, 0, 5, 0, 160, 3.4, 2.8],
  [17, 26, 3, 3, 6, 60, 240, 2.9, 2.2],
  [31, 15, 3, 1, 9, -120, 60, 3.6, 3.0],
  [45, 12, 3, 3, 5, 30, 210, 2.5, 1.8],
  [6, 24, 4, 2, 6, 180, 0, 3.2, 2.6],
]
const style = (d: (typeof DOTS)[number]) => ({
  left: `${d[0]}px`, top: `${d[1]}px`, '--r': `${d[4]}px`, '--a0': `${d[5]}deg`, '--a1': `${d[6]}deg`, '--t': `${d[7]}s`, '--b': `${d[8]}s`,
  '--d': `${d[2]}px`, '--c': `var(${COLORS[d[3]]})`,
})
</script>

<template>
  <span v-if="fx !== 'off'" class="dots" :class="{ play: playing }" aria-hidden="true">
    <span v-for="(d, i) in DOTS" :key="i" class="orbit" :style="style(d)"><i class="dot" /></span>
  </span>
</template>

<style scoped>
.dots { position: relative; display: inline-block; flex: none; width: 58px; height: 34px; vertical-align: middle; }
.orbit { position: absolute; width: 0; height: 0; transform: rotate(var(--a0)) translateX(var(--r)); }
.dot { position: absolute; left: calc(var(--d) / -2); top: calc(var(--d) / -2); width: var(--d); height: var(--d); border-radius: 50%; background: var(--c); opacity: .8; }
.play .orbit { animation: dots-orbit var(--t) ease-in-out infinite alternate; }
.play .dot { animation: dots-breathe var(--b) ease-in-out infinite alternate; }
@keyframes dots-orbit { to { transform: rotate(var(--a1)) translateX(var(--r)); } }
@keyframes dots-breathe { from { opacity: .45; } to { opacity: 1; } }
</style>
