<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, toRef, watch } from 'vue'
import { useFx, useLoop, watchFrames } from '@/utils/motion'
// 解析等待的品牌时刻（V33-C-05）：小纸面上扫描带自上而下扫过，扫过的行落黑条、一行出现代号，末了盖“已脱敏”章。
// 它是状态指示，不是数据视图，哪几行变黑条写死。文字（已用时间、本机正在做什么）由所在页面显示。
// 完整：循环；精简：定格终态；关闭：不显示。
const props = defineProps<{ active: boolean }>()
const fx = useFx()
const playing = useLoop(toRef(props, 'active'))
const shown = computed(() => props.active && fx.value !== 'off')

let stopFrames = () => {}
function start() { stopFrames(); stopFrames = watchFrames() }
watch(playing, value => { if (value) start() })
onMounted(() => { if (playing.value) start() })
onBeforeUnmount(() => stopFrames())
</script>

<template>
  <div v-if="shown" class="si-sheet scan-doc si-wait" :class="{ play: playing }" aria-hidden="true">
    <span class="si-sheet-stamp stamp">已脱敏</span>
    <span class="si-sheet-line" style="width: 78%" />
    <span class="si-sheet-line b b1" style="width: 56%" />
    <span class="si-sheet-line" style="width: 90%" />
    <span class="row"><span class="si-sheet-line" style="width: 30%" /><span class="si-sheet-chip b b2">[机构_1]</span><span class="si-sheet-line" style="flex: 1" /></span>
    <span class="si-sheet-line" style="width: 84%" />
    <span class="si-sheet-line b b3" style="width: 64%" />
    <span class="si-sheet-line b b4" style="width: 72%" />
    <span class="si-sheet-line" style="width: 40%" />
    <span class="band" />
  </div>
</template>

<style scoped>
.scan-doc { gap: 10px; width: 200px; height: 150px; padding: 16px 18px; overflow: visible; box-sizing: border-box; }
.row { display: flex; align-items: center; gap: 6px; }
.row .si-sheet-line { min-width: 0; }
.si-sheet-chip { flex: none; line-height: 1.4; }
.stamp { right: -14px; bottom: -12px; color: var(--si-ok); }
.band { position: absolute; left: 0; right: 0; top: 0; height: 24px; margin-top: -24px; border-radius: inherit; background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--si-blue) 22%, transparent) 85%, var(--si-blue)); opacity: 0; pointer-events: none; }

/* 终态是默认样式（精简档定格于此）；.play 时循环 4.8s：扫描 0～60%，印章 66% 落下，92% 起整体淡出重来 */
.b { position: relative; }
.b:not(.si-sheet-chip)::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: var(--si-redact-bar); }
.play .band { animation: sd-scan 4.8s linear infinite; }
.play .b1::after { animation: sd-b1 4.8s var(--si-ease) infinite; }
.play .b2 { animation: sd-b2 4.8s var(--si-ease) infinite; }
.play .b3::after { animation: sd-b3 4.8s var(--si-ease) infinite; }
.play .b4::after { animation: sd-b4 4.8s var(--si-ease) infinite; }
.play .stamp { animation: sd-stamp 4.8s var(--si-ease) infinite; }
@keyframes sd-scan { 0% { transform: translateY(0); opacity: 0; } 4% { opacity: 1; } 58% { opacity: 1; } 62%, 100% { transform: translateY(calc(150px + 24px)); opacity: 0; } }
@keyframes sd-b1 { 0%, 14% { opacity: 0; } 18%, 92% { opacity: 1; } 98%, 100% { opacity: 0; } }
@keyframes sd-b2 { 0%, 28% { opacity: 0; } 32%, 92% { opacity: 1; } 98%, 100% { opacity: 0; } }
@keyframes sd-b3 { 0%, 42% { opacity: 0; } 46%, 92% { opacity: 1; } 98%, 100% { opacity: 0; } }
@keyframes sd-b4 { 0%, 50% { opacity: 0; } 54%, 92% { opacity: 1; } 98%, 100% { opacity: 0; } }
@keyframes sd-stamp { 0%, 64% { opacity: 0; transform: rotate(-8deg) scale(1.3); } 70%, 92% { opacity: .55; transform: rotate(-8deg) scale(1); } 98%, 100% { opacity: 0; transform: rotate(-8deg) scale(1); } }
</style>
