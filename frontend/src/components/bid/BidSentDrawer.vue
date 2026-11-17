<script setup lang="ts">
import { ref, watch } from 'vue'
import { bidApi } from '@/api/bid'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'

const props = defineProps<{ projectId: string }>()
const open = defineModel<boolean>({ required: true })
const items = ref<Awaited<ReturnType<typeof bidApi.sent>>>([]), loading = ref(false), error = ref('')
let generation = 0
watch([open, () => props.projectId], async ([visible]) => {
  const current = ++generation
  if (!visible) return
  loading.value = true; error.value = ''; items.value = []
  try { const data = await bidApi.sent(props.projectId); if (current === generation) items.value = data }
  catch (e) { if (current === generation) error.value = e instanceof ApiError ? e.message : '读取失败，请重试' }
  finally { if (current === generation) loading.value = false }
}, { immediate: true })
const status = { sent: '已发送', blocked: '拦截', error: '发送失败' }
</script>

<template>
  <el-drawer v-model="open" size="640px" append-to-body title="实际发送内容">
    <p class="hint">发送时的原样文字，含代号，在本机不还原；拦截记录没有发送给模型。</p>
    <div v-loading="loading" class="body">
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-else-if="!loading && !items.length" class="hint">本项目没有发送记录。</p>
      <section v-for="item in items" :key="item.seq">
        <h3>{{ item.purpose }} · {{ status[item.status] }}</h3>
        <p class="hint">{{ formatDateTime(item.created_at) }} · {{ item.chars === null ? (item.status === 'blocked' ? '未保存正文' : '正文已清理') : `${item.chars} 字` }}</p>
        <div v-if="item.text !== null" class="si-paper sheet">{{ item.text }}</div>
        <p v-else class="hint">{{ item.status === 'blocked' ? '请求已拦截，未保存正文。' : '正文已按保留期清理。' }}</p>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped>
.body { display: flex; flex-direction: column; gap: 24px; min-height: 100px; }
h3 { margin: 0; font: var(--si-font-label); color: var(--si-ink-1); }
.hint { margin: 6px 0 12px; font: var(--si-font-meta); color: var(--si-ink-3); }
.sheet { padding: 16px 20px; font: var(--si-font-body); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
