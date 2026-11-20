<script setup lang="ts">
// 页签开头的结论句；sub 为其下一行小字
defineProps<{ text?: string; sub?: string | null }>()
</script>

<template>
  <p class="bid-lede">
    <template v-if="text">{{ text }}</template>
    <span v-if="sub" class="sub num">{{ sub }}</span>
    <slot />
  </p>
</template>
