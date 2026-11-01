<script setup lang="ts">
import { computed } from 'vue'
import { useFx } from '@/utils/motion'

/** 滚动数字（V31-U1 第五节）：只用于整数计数。完整特效逐位滚动，精简淡入，关闭直接显示。 */
const props = defineProps<{ value: number }>()
const fx = useFx()
const text = computed(() => String(Math.trunc(props.value)))
const chars = computed(() => Array.from(text.value))
</script>

<template>
  <span class="si-count" role="text" :aria-label="text">
    <template v-if="fx === 'full'">
      <span v-for="(c, i) in chars" :key="chars.length - i" class="slot" aria-hidden="true">
        <span v-if="/\d/.test(c)" class="reel" :style="{ transform: `translateY(${-Number(c)}em)` }"><span v-for="d in 10" :key="d">{{ d - 1 }}</span></span>
        <span v-else>{{ c }}</span>
      </span>
    </template>
    <Transition v-else-if="fx === 'lite'" name="si-fade" mode="out-in"><span :key="text" aria-hidden="true">{{ text }}</span></Transition>
    <span v-else aria-hidden="true">{{ text }}</span>
  </span>
</template>

<style scoped>
.si-count { display: inline-flex; font-family: var(--si-font-mono); font-variant-numeric: tabular-nums; line-height: 1; }
.slot { display: inline-block; height: 1em; overflow: hidden; }
.reel { display: flex; flex-direction: column; transition: transform 1.2s var(--si-ease); }
.reel > span { display: block; height: 1em; }
</style>
