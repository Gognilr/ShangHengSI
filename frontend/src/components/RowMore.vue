<script setup lang="ts">
import { MoreHorizontal } from '@lucide/vue'
// 表格行尾“⋯”下拉（V3-A 4.4）：下拉项由插槽给出，删除放最后并用 divided / 危险色
defineProps<{ disabled?: boolean }>()
</script>

<template>
  <el-dropdown trigger="click" :disabled="disabled">
    <el-button link type="primary" :disabled="disabled" aria-label="更多操作"><MoreHorizontal :size="16" :stroke-width="1.75" /></el-button>
    <template #dropdown><el-dropdown-menu><slot /></el-dropdown-menu></template>
  </el-dropdown>
</template>
