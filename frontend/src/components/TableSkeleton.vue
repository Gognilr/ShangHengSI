<script setup lang="ts">
// 表格首次加载的骨架（V3-A 4.4）：columns 为各列宽度（如 '170px'、'1fr'），与真实表格列宽对应
withDefaults(defineProps<{ columns: string[]; rows?: number }>(), { rows: 5 })
</script>

<template>
  <el-skeleton animated class="table-skeleton" aria-busy="true">
    <template #template>
      <div v-for="row in rows + 1" :key="row" class="skeleton-row" :class="{ head: row === 1 }" :style="{ gridTemplateColumns: columns.join(' ') }">
        <el-skeleton-item v-for="(_, index) in columns" :key="index" variant="text" />
      </div>
    </template>
  </el-skeleton>
</template>

<style scoped>
.skeleton-row { display: grid; gap: 16px; align-items: center; height: 52px; padding: 0 12px; border-bottom: 1px solid var(--el-border-color-lighter); }
.skeleton-row.head { height: 40px; background: var(--el-fill-color-light); }
.skeleton-row :deep(.el-skeleton__item) { height: 14px; }
</style>
