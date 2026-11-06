<script setup lang="ts">
// 新 IP 登录提醒（V31-U5F 7.2）：每分钟查一次本会话有没有提醒（只查不顺延会话），有就在页面顶部提示。
import { ElMessage } from 'element-plus'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi, type LoginNotice } from '@/api/auth'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'

const router = useRouter()
const notice = ref<LoginNotice | null>(null)
const busy = ref(false)
let timer: number | undefined

async function poll() {
  if (document.visibilityState !== 'visible') return
  try {
    const n = await authApi.notice()
    notice.value = n.ip ? n : null
  } catch { /* 网络抖动或已退出：下一次正常请求会处理 */ }
}
function onVisible() { if (document.visibilityState === 'visible') void poll() }
onMounted(() => {
  void poll()
  timer = window.setInterval(poll, 60_000)
  document.addEventListener('visibilitychange', onVisible)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisible)
})

async function dismiss() {
  notice.value = null
  try { await authApi.dismissNotice() } catch { /* 下次查询时再显示 */ }
}
async function endOthers() {
  busy.value = true
  try {
    await authApi.endOtherDevices()
    notice.value = null
    ElMessage.success('其他设备已全部退出，建议立即修改密码')
    window.dispatchEvent(new Event('si:devices-changed'))  // 已在“个人”页时刷新设备列表
    await router.push('/settings/personal')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '操作失败，请稍后重试')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="notice" class="login-notice" role="alert">
    <span class="text">你的账号刚在 <b class="num">{{ notice.ip }}</b> 登录（{{ formatDateTime(notice.at) }}），如非本人请退出其他设备并修改密码。</span>
    <el-button size="small" type="warning" :loading="busy" @click="endOthers">退出其他设备</el-button>
    <el-button size="small" text @click="dismiss">是我本人</el-button>
  </div>
</template>

<style scoped>
.login-notice {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 24px; border-bottom: 1px solid var(--si-hair);
  background: var(--el-color-warning-light-9); color: var(--si-ink-1); font: var(--si-font-body);
}
.login-notice .text { flex: 1; min-width: 240px; }
</style>
