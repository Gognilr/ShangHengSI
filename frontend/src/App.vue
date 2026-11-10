<script setup lang="ts">
import { ServerOff } from '@lucide/vue'
import EmptyState from '@/components/EmptyState.vue'
import SiToast from '@/components/ui/SiToast.vue'
import { useAuthStore } from '@/stores/auth'
// 短提示统一显示在右下角（V3-A 4.3）
const messageConfig = { placement: 'bottom-right' as const }
const auth = useAuthStore()
</script>

<template>
  <el-config-provider :message="messageConfig">
    <!-- 首次打开时连不上服务器：路由在等待重试，这里给出提示，恢复后自动进入原页面（V31Q-113） -->
    <div v-if="auth.unavailable" class="server-unavailable" role="alert">
      <EmptyState :icon="ServerOff" title="暂时连不上服务器"
                  description="服务器可能正在启动或升级，每 5 秒自动重试一次，恢复后自动打开页面。长时间没有恢复请联系系统管理员。">
        <el-button @click="auth.retryNow()">立即重试</el-button>
      </EmptyState>
    </div>
    <router-view />
    <SiToast />
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

.server-unavailable {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
