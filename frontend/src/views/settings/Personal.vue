<script setup lang="ts">
// 设置 · 个人（V31Q-95）：所有人可见；外观只存本浏览器，改密走原有对话框；登录设备（V31-U5F 7.2）
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi, type Device } from '@/api/auth'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/time'
import AppearancePanel from '@/components/AppearancePanel.vue'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import SettingSection from '@/components/SettingSection.vue'

const auth = useAuthStore()
const router = useRouter()
const pwdOpen = ref(false)

const devices = ref<Device[]>([])
const devicesError = ref('')
const busy = ref(false)
async function loadDevices() {
  devicesError.value = ''
  try { devices.value = await authApi.devices() }
  catch (e) { devicesError.value = e instanceof ApiError ? `加载失败：${e.message}` : '加载失败，请刷新页面' }
}
onMounted(() => { void loadDevices(); window.addEventListener('si:devices-changed', loadDevices) })
onBeforeUnmount(() => window.removeEventListener('si:devices-changed', loadDevices))

async function endDevice(d: Device) {
  if (d.current) {
    try {
      await ElMessageBox.confirm('退出本机后需要重新登录。', '退出此设备', { type: 'warning', confirmButtonText: '退出', cancelButtonText: '取消' })
    } catch { return }
  }
  busy.value = true
  try {
    await authApi.endDevice(d.id)
    if (d.current) {
      auth.clear()
      await router.replace({ name: 'login' })
      return
    }
    ElMessage.success('已退出该设备')
    await loadDevices()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试')
  } finally {
    busy.value = false
  }
}

async function endOthers() {
  busy.value = true
  try {
    await authApi.endOtherDevices()
    ElMessage.success('其他设备已全部退出')
    await loadDevices()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="settings-page">
    <PageHeader title="个人" description="只影响你自己的使用；外观保存在本浏览器，不发送到服务器" />
    <div class="panel setting-panel">
      <SettingSection title="外观" description="外壳与特效，修改立即生效。">
        <AppearancePanel />
      </SettingSection>
      <SettingSection title="账号" description="账号由管理员统一分发；修改密码后其他设备会自动退出。">
        <dl class="info">
          <div><dt>显示名</dt><dd>{{ auth.user?.display_name }}</dd></div>
          <div><dt>用户名</dt><dd>{{ auth.user?.username }}</dd></div>
          <div><dt>角色</dt><dd>{{ auth.user?.roles.join('、') || '无' }}</dd></div>
        </dl>
        <el-button @click="pwdOpen = true">修改密码</el-button>
      </SettingSection>
      <SettingSection title="登录设备" description="同一账号可以在多处登录。不认识的设备请退出，并修改密码。" stacked>
        <el-alert v-if="devicesError" type="error" :closable="false" show-icon :title="devicesError" class="gap" />
        <ul class="devices">
          <li v-for="d in devices" :key="d.id">
            <div class="main">
              <div class="title">{{ d.browser }}<el-tag v-if="d.current" size="small" type="success" class="me">本机</el-tag></div>
              <div class="meta num">{{ d.ip ?? '地址未知' }} · 登录于 {{ formatDateTime(d.created_at) }} · 最近活动 {{ formatDateTime(d.last_seen_at) }}</div>
            </div>
            <el-button size="small" :disabled="busy" @click="endDevice(d)">退出此设备</el-button>
          </li>
        </ul>
        <el-button v-if="devices.length > 1" :disabled="busy" @click="endOthers">退出其他全部设备</el-button>
      </SettingSection>
    </div>
    <ChangePasswordDialog v-model="pwdOpen" />
  </div>
</template>

<style scoped>
.info { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 0 0 20px; }
dt { font: var(--si-font-meta); color: var(--si-ink-3); }
dd { margin: 4px 0 0; font: var(--si-font-body); color: var(--si-ink-1); overflow-wrap: anywhere; }
.devices { list-style: none; margin: 0 0 12px; padding: 0; }
.devices li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--si-hair); }
.devices li:last-child { border-bottom: 0; }
.devices .main { flex: 1; min-width: 0; }
.devices .title { font: var(--si-font-body); color: var(--si-ink-1); display: flex; align-items: center; gap: 8px; }
.devices .meta { font: var(--si-font-meta); color: var(--si-ink-3); margin-top: 2px; overflow-wrap: anywhere; }
</style>
