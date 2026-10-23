<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { User, Cpu, List, Lock, Coin } from '@element-plus/icons-vue'

// 设置页导航与原子路由一一对应，权限仍由路由 meta.admin 校验。
const tabs = [
  { name: 'users', label: '账号', icon: User },
  { name: 'model', label: '模型', icon: Cpu },
  { name: 'requirements', label: '审核要求', icon: List },
  { name: 'redaction', label: '脱敏', icon: Lock },
  { name: 'data', label: '数据', icon: Coin },
]

const route = useRoute()
const router = useRouter()

const activeTab = computed(() => {
  const hit = tabs.find((t) => route.path === `/settings/${t.name}`)
  return hit ? hit.name : 'users'
})

function onTabChange(name: string | number) {
  router.push(`/settings/${name}`)
}
</script>

<template>
  <div class="page page--wide settings">
    <aside class="panel settings-nav" aria-label="设置导航">
      <el-menu :default-active="activeTab" @select="onTabChange"><el-menu-item v-for="t in tabs" :key="t.name" :index="t.name"><el-icon><component :is="t.icon" /></el-icon><span>{{ t.label }}</span></el-menu-item></el-menu>
    </aside>
    <div class="settings-content"><router-view /></div>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}
.settings-nav { flex: 0 0 220px; box-sizing: border-box; padding: 12px; position: sticky; top: 32px; }
/* 主滚动区从顶栏下开始，32px 对应视口顶部的 64 + 32 = 96px。 */
.settings-nav :deep(.el-menu) { border-right: 0; }
.settings-nav :deep(.el-menu-item) { height: 44px; border-radius: 8px; }
.settings-nav :deep(.el-menu-item.is-active) { background: var(--el-color-primary-light-9); font-weight: 600; }
.settings-content { flex: 1; min-width: 0; }
</style>
