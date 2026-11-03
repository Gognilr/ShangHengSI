<script setup lang="ts">
import { ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { runsApi, type SentItem } from '@/api/runs'

/** 本轮实际发送的文字（V31-U3 第二节）：只读，原样显示代号，在本机不还原；打开时才请求。 */
const props = defineProps<{ runId: string }>()
const open = defineModel<boolean>({ required: true })
const items = ref<SentItem[] | null>(null)
const cleared = ref(false)
const loading = ref(false)
const error = ref('')
let loadedFor = ''
let version = 0

async function load() {
  const current = ++version
  loading.value = true
  error.value = ''
  items.value = null
  cleared.value = false
  try {
    const result = await runsApi.sent(props.runId)
    if (current !== version) return
    if (Array.isArray(result)) items.value = result
    else cleared.value = true
    loadedFor = props.runId
  } catch (e) {
    if (current !== version) return
    error.value = e instanceof ApiError ? `${e.message}，请稍后重试` : '加载失败，请稍后重试'
  } finally {
    if (current === version) loading.value = false
  }
}

watch([open, () => props.runId], ([isOpen]) => { if (isOpen && loadedFor !== props.runId) void load() })
</script>

<template>
  <el-drawer v-model="open" size="640px" direction="rtl" append-to-body class="sent-drawer">
    <template #header>
      <div class="header">
        <h2 class="title">本轮实际发送的文字</h2>
        <p class="subtitle">这是发出去的原样，含代号，在本机不还原</p>
      </div>
    </template>
    <div v-loading="loading" class="body">
      <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
      <p v-else-if="cleared" class="empty">外发记录正文已按保留期清理</p>
      <p v-else-if="items && !items.length" class="empty">本轮没有发送记录</p>
      <section v-for="item in items ?? []" :key="item.seq" class="block">
        <div class="meta">第 {{ item.seq }} 次 · {{ item.chars }} 字</div>
        <div class="si-paper sheet">{{ item.text }}</div>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.header { min-width: 0; }
.title { margin: 0; font: var(--si-font-h2); color: var(--el-text-color-primary); }
.subtitle { margin: 2px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.body { display: flex; flex-direction: column; gap: 16px; min-height: 120px; }
.block { display: flex; flex-direction: column; gap: 6px; }
.meta { font: var(--si-font-label-sm); color: var(--si-ink-3); font-variant-numeric: tabular-nums; }
.sheet { padding: 16px 20px; border-radius: var(--si-radius-paper); box-shadow: var(--si-paper-shadow); font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
.empty { margin: 24px 0; text-align: center; font: var(--si-font-body); color: var(--si-ink-3); }
</style>
