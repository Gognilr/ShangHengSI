<script setup lang="ts">
import type { LedePart } from './results'

// 页签开头的结论句（V34Q-25 第 1 条）：分句以“；”相连、句号结尾；sub 为其下一行小字
defineProps<{ parts: LedePart[]; sub?: string }>()
</script>

<template>
  <p class="bid-lede">
    <template v-for="(part, i) in parts" :key="i"><template v-for="(seg, j) in part" :key="j"><span v-if="Array.isArray(seg)" :class="seg[1]">{{ seg[0] }}</span><template v-else>{{ seg }}</template></template>{{ i < parts.length - 1 ? '；' : '。' }}</template>
    <span v-if="sub" class="sub num">{{ sub }}</span>
    <slot />
  </p>
</template>
