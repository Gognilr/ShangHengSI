<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const pwdOpen = ref(false)
// 须改密期间对话框始终打开且不可关闭
const pwdVisible = computed(() => pwdOpen.value || auth.mustChangePassword)

// 顶部菜单只有两项（SPEC 1.6）；“设置”不在菜单里，是右上角仅管理员可见的入口
const activeMenu = computed(() => (route.path.startsWith('/history') ? '/history' : '/review/new'))

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
      <div class="brand">企业商务文件AI智能审查助手</div>
      <el-menu mode="horizontal" :default-active="activeMenu" router :ellipsis="false" class="menu">
        <el-menu-item index="/review/new">智能审核</el-menu-item>
        <el-menu-item index="/history">审核历史</el-menu-item>
      </el-menu>
      <div class="right">
        <router-link v-if="auth.isAdmin" to="/settings" class="settings-link">设置</router-link>
        <el-dropdown trigger="click" @command="onCommand">
          <span class="user">{{ auth.user?.display_name }} ▾</span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <!-- 管理员提醒条（SPEC 6.7）；提醒内容在 M7 接入，这里只留位置 -->
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
  min-height: 100%;
  flex-direction: column;
}

.header {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 24px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}

.brand {
  font-size: 18px;
  font-weight: 600;
  margin-right: 32px;
  white-space: nowrap;
}

.menu {
  flex: 1;
  border-bottom: none;
  height: 56px;
}

.right {
  display: flex;
  align-items: center;
  gap: 24px;
}

.settings-link {
  color: var(--el-text-color-regular);
  text-decoration: none;
}

.settings-link:hover {
  color: var(--el-color-primary);
}

.user {
  cursor: pointer;
  color: var(--el-text-color-regular);
}

.main {
  padding: 0;
}
</style>
