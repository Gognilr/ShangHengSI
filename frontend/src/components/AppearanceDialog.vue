<script setup lang="ts">
import AppearancePanel from '@/components/AppearancePanel.vue'

/** 用户菜单“外观”的快捷对话框；完整设置在“设置 · 个人”。 */
const visible = defineModel<boolean>({ required: true })
</script>

<template>
  <el-dialog v-model="visible" title="外观" width="420px" append-to-body>
    <AppearancePanel />
  </el-dialog>
</template>
