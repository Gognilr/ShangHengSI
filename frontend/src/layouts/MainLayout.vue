<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import SiLogo from '@/components/brand/SiLogo.vue'
import { Setting } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const pwdOpen = ref(false)
// 须改密期间对话框始终打开且不可关闭
const pwdVisible = computed(() => pwdOpen.value || auth.mustChangePassword)

// 顶部菜单只有两项（SPEC 1.6）；“设置”不在菜单里，是右上角仅管理员可见的入口
const activeMenu = computed(() => route.path.startsWith('/settings') ? '' : (route.path.startsWith('/history') ? '/history' : '/review/new'))
const displayName = computed(() => auth.user?.display_name ?? '')
const initial = computed(() => Array.from(displayName.value)[0] ?? '')

async function onCommand(cmd: string) {
  if (cmd === 'password') {
    pwdOpen.value = true
  } else if (cmd === 'logout') {
    await auth.logout()
    await router.replace({ name: 'login' })
  }
}
</script>

<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="header-inner">
        <SiLogo show-tagline />
        <el-menu mode="horizontal" :default-active="activeMenu" router :ellipsis="false" class="menu">
          <el-menu-item index="/review/new">智能审核</el-menu-item>
          <el-menu-item index="/history">审核历史</el-menu-item>
        </el-menu>
        <div class="right">
          <router-link v-if="auth.isAdmin" to="/settings" class="settings-link" :class="{ selected: route.path.startsWith('/settings') }">
            <el-icon><Setting /></el-icon>设置
          </router-link>
          <el-dropdown trigger="click" @command="onCommand">
            <span class="user"><span class="avatar">{{ initial }}</span><span class="display-name">{{ displayName }}</span> ▾</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </el-header>

    <!-- 管理员提醒条（SPEC 6.7），沿用登录状态返回的提醒内容。 -->
    <div v-if="auth.isAdmin && auth.alerts.length" class="alerts">
      <el-alert
        v-for="(a, i) in auth.alerts"
        :key="i"
        type="warning"
        show-icon
        :closable="false"
        :title="a.message"
      />
    </div>

    <el-main class="main">
      <router-view />
    </el-main>

    <ChangePasswordDialog
      :model-value="pwdVisible"
      :forced="auth.mustChangePassword"
      @update:model-value="pwdOpen = $event"
    />
  </el-container>
</template>

<style scoped>
.layout {
  /* 整体固定为视口高度，内容在 .main 内滚动：外层不出现滚动条，避免挤占 body 的 1280px 最小宽度 */
  height: 100vh;
  flex-direction: column;
}

.header {
  flex-shrink: 0;
  height: 64px;
  padding: 0;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.header-inner {
  display: flex;
  align-items: center;
  gap: 32px;
  max-width: 1600px;
  height: 100%;
  margin: 0 auto;
  padding: 0 32px;
  box-sizing: border-box;
}

.menu {
  flex: 1;
  border-bottom: none;
  height: 63px;
  min-width: 240px;
  background: transparent;
}
.menu :deep(.el-menu-item) { position: relative; border-bottom: none; padding: 0 16px; font-size: 15px; }
.menu :deep(.el-menu-item.is-active) { color: var(--el-color-primary); font-weight: 600; }
.menu :deep(.el-menu-item.is-active)::after { content: ''; position: absolute; left: 16px; right: 16px; bottom: 0; height: 2px; background: var(--si-brand-gradient); }
.menu :deep(.el-menu-item:hover), .menu :deep(.el-menu-item:focus) { background: transparent; color: var(--el-color-primary); }
.menu :deep(.el-menu-item:focus-visible) { outline: 2px solid var(--el-color-primary-light-5); outline-offset: -4px; }

.right {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-left: auto;
  flex: none;
}

.settings-link {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border-radius: var(--el-border-radius-base);
  color: var(--el-text-color-regular);
  text-decoration: none;
}

.settings-link:hover, .settings-link.selected {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.user {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--el-text-color-regular);
}
.display-name { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.alerts {
  flex-shrink: 0;
  background: var(--el-color-warning-light-9);
  border-bottom: 1px solid var(--el-color-warning-light-7);
}
.alerts :deep(.el-alert) { max-width: 1600px; margin: 0 auto; padding: 8px 32px; border-radius: 0; box-sizing: border-box; }

.main {
  padding: 0;
  min-height: 0;
}
</style>
