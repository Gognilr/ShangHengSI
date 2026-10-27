<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>
<template>
  <div class="page-header">
    <div><h1>{{ title }}</h1><p v-if="description">{{ description }}</p></div>
    <div v-if="$slots.default" class="page-header-actions"><slot /></div>
  </div>
</template>
<style scoped>
.page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
h1 { margin: 0; font: var(--si-font-title); }
p { margin: 4px 0 0; font: var(--si-font-body); color: var(--el-text-color-secondary); }
.page-header-actions { flex: none; }
</style>
