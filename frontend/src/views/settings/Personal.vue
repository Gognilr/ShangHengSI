<script setup lang="ts">
// 设置 · 个人（V31Q-95）：所有人可见；外观只存本浏览器，改密走原有对话框
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { roleLabel } from '@/utils/labels'
import AppearancePanel from '@/components/AppearancePanel.vue'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import SettingSection from '@/components/SettingSection.vue'

const auth = useAuthStore()
const pwdOpen = ref(false)
</script>

<template>
  <div class="settings-page">
    <PageHeader title="个人" description="只影响你自己的使用；外观保存在本浏览器，不发送到服务器" />
    <div class="panel setting-panel">
      <SettingSection title="外观" description="外壳与特效，修改立即生效。">
        <AppearancePanel />
      </SettingSection>
      <SettingSection title="账号" description="账号由管理员统一分发。">
        <dl class="info">
          <div><dt>显示名</dt><dd>{{ auth.user?.display_name }}</dd></div>
          <div><dt>用户名</dt><dd>{{ auth.user?.username }}</dd></div>
          <div><dt>角色</dt><dd>{{ roleLabel(auth.user?.role ?? '') }}</dd></div>
        </dl>
        <el-button @click="pwdOpen = true">修改密码</el-button>
      </SettingSection>
    </div>
    <ChangePasswordDialog v-model="pwdOpen" />
  </div>
</template>

<style scoped>
.info { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 0 0 20px; }
dt { font: var(--si-font-meta); color: var(--si-ink-3); }
dd { margin: 4px 0 0; font: var(--si-font-body); color: var(--si-ink-1); overflow-wrap: anywhere; }
</style>
