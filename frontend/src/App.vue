<script setup lang="ts">
// 短提示统一显示在右下角（V3-A 4.3）
const messageConfig = { placement: 'bottom-right' as const }
</script>

<template>
  <el-config-provider :message="messageConfig">
    <router-view />
  </el-config-provider>
</template>

<style>
html,
body,
#app {
  margin: 0;
  height: 100%;
}

body {
  /* 桌面端，最小宽度 1280 像素（SPEC 21） */
  min-width: 1280px;
  background: var(--si-bg-canvas);
}
</style>
