<script setup lang="ts">
// 页面底部的“有未保存的修改”粘底提示栏（V3-A 5）
defineProps<{ dirty: boolean; saving: boolean }>()
defineEmits<{ save: []; discard: [] }>()
</script>

<template>
  <transition name="unsaved">
    <div v-if="dirty" class="unsaved-bar" role="region" aria-label="未保存的修改">
      <span>有未保存的修改</span>
      <span class="buttons">
        <el-button :disabled="saving" @click="$emit('discard')">放弃</el-button>
        <el-button type="primary" :loading="saving" @click="$emit('save')">保存</el-button>
      </span>
    </div>
  </transition>
</template>

<style scoped>
.unsaved-bar { position: sticky; bottom: 16px; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 16px; padding: 12px 16px; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); box-shadow: var(--el-box-shadow); font: var(--si-font-body); }
.unsaved-enter-active, .unsaved-leave-active { transition: opacity 150ms, transform 150ms; }
.unsaved-enter-from, .unsaved-leave-to { opacity: 0; transform: translateY(8px); }
</style>
