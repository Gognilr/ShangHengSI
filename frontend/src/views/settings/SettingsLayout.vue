<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// 设置页 5 个标签页（SPEC 15），标签与子路由一一对应
const tabs = [
  { name: 'users', label: '账号' },
  { name: 'model', label: '模型' },
  { name: 'requirements', label: '审核要求' },
  { name: 'redaction', label: '脱敏' },
  { name: 'data', label: '数据' },
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
  <div class="settings">
    <h2 class="settings-title">设置</h2>
    <el-tabs :model-value="activeTab" @update:model-value="onTabChange">
      <el-tab-pane v-for="t in tabs" :key="t.name" :label="t.label" :name="t.name" />
    </el-tabs>
    <router-view />
  </div>
</template>

<style scoped>
.settings {
  padding: 24px;
}

.settings-title {
  margin: 0 0 8px;
}
</style>
