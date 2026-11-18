<script setup lang="ts">
// 设置 · 个人（V31Q-95）：所有人可见；外观只存本浏览器，改密走原有对话框；登录设备（V31-U5F 7.2）
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi, type Device } from '@/api/auth'
import { bidApi, type BidFeedback } from '@/api/bid'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/time'
import AppearancePanel from '@/components/AppearancePanel.vue'
import ChangePasswordDialog from '@/components/ChangePasswordDialog.vue'
import SettingSection from '@/components/SettingSection.vue'

const auth = useAuthStore()
const router = useRouter()
const pwdOpen = ref(false)
const feedback = ref<BidFeedback>(), feedbackError = ref('')
async function loadFeedback() {
  if (!auth.can('use.bid')) return
  try { feedback.value = await bidApi.feedback(); feedbackError.value = '' }
  catch { feedbackError.value = '标书反馈读取失败' }
}
async function revokeFeedback(id: string) {
  try { await bidApi.revokeFeedback(id); await loadFeedback() }
  catch { feedbackError.value = '撤销失败，请重试' }
}
onMounted(loadFeedback)

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
    <div class="panel setting-panel">
      <SettingSection title="外观" description="只保存在本浏览器，修改立即生效；文件纸面跟随外壳。">
        <div class="narrow"><AppearancePanel /></div>
      </SettingSection>
      <SettingSection v-if="auth.can('use.bid')" title="标书初审反馈" description="按招标代理机构记住同源证据，仅本人可见。">
        <p v-if="feedbackError" role="alert">{{ feedbackError }}</p>
        <p v-if="feedback && !feedback.rules.length" class="si-muted">没有已记住的反馈。</p>
        <div v-for="rule in feedback?.rules" :key="rule.id" class="si-row">
          <span class="si-row-value">{{ rule.agency }} · {{ rule.label }} · {{ rule.reason }}</span>
          <el-button text size="small" @click="revokeFeedback(rule.id)">撤销</el-button>
        </div>
        <div v-for="row in feedback?.stats" :key="row.category" class="si-row">
          <span class="si-row-value">{{ row.category }}属实率 {{ row.verified }} / {{ row.total }}<template v-if="row.total >= 5 && row.verified * 2 < row.total"> · 属实率偏低</template></span>
        </div>
      </SettingSection>
      <SettingSection title="账号" description="账号由管理员统一分发；修改密码后其他设备会自动退出。">
        <div class="si-rows narrow">
          <div class="si-row"><span class="si-row-label">显示名</span><span class="si-row-value">{{ auth.user?.display_name }}</span></div>
          <div class="si-row"><span class="si-row-label">用户名</span><span class="si-row-value">{{ auth.user?.username }}</span></div>
          <div class="si-row"><span class="si-row-label">角色</span><span class="si-row-value tags"><el-tag v-for="r in auth.user?.roles ?? []" :key="r" type="info" size="small">{{ r }}</el-tag><template v-if="!auth.user?.roles.length">无</template></span></div>
          <div class="si-row"><span class="si-row-label">密码</span><span class="si-row-value dots" aria-hidden="true">••••••••</span><el-button link type="primary" @click="pwdOpen = true">修改密码</el-button></div>
        </div>
      </SettingSection>
      <SettingSection title="登录设备" description="同一账号可以在多处登录。不认识的设备请退出，并修改密码。">
        <template v-if="devices.length > 1" #actions><el-button size="small" :disabled="busy" @click="endOthers">退出其他全部设备</el-button></template>
        <el-alert v-if="devicesError" type="error" :closable="false" show-icon :title="devicesError" />
        <ul class="si-rows narrow devices">
          <li v-for="d in devices" :key="d.id" class="si-row">
            <div class="si-row-value">
              <div class="title">{{ d.browser }}<el-tag v-if="d.current" size="small" type="success">本机</el-tag></div>
              <div class="meta num">{{ d.ip ?? '地址未知' }} · 登录于 {{ formatDateTime(d.created_at) }} · 最近活动 {{ formatDateTime(d.last_seen_at) }}</div>
            </div>
            <el-button link type="primary" :disabled="busy" @click="endDevice(d)">退出</el-button>
          </li>
        </ul>
      </SettingSection>
    </div>
    <ChangePasswordDialog v-model="pwdOpen" />
  </div>
</template>

<style scoped>
.narrow { max-width: 560px; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.dots { color: var(--si-ink-3); letter-spacing: 2px; }
.devices { list-style: none; margin: 0; padding: 0; }
.devices .si-row { align-items: center; }
.devices .title { display: flex; align-items: center; gap: 8px; color: var(--si-ink-1); }
.devices .meta { margin-top: 2px; font: var(--si-font-meta); color: var(--si-ink-3); overflow-wrap: anywhere; }
</style>
