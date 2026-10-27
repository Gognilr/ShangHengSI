<template>
  <router-view />
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
  background: var(--el-bg-color-page);
}
</style>
