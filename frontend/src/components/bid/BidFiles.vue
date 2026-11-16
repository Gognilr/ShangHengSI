<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { BidFile } from '@/api/bid'
import { bidApi } from '@/api/bid'
import { ApiError } from '@/api/http'

const props = defineProps<{ projectId: string; files: BidFile[]; disabled: boolean; rowErrors?: Record<string, string> }>()
const emit = defineEmits<{ password: [fid: string, password: string]; remove: [fid: string] }>()
const passwords = reactive<Record<string, string>>({}), shown = reactive<Record<string, string>>({}), editing = reactive<Record<string, boolean>>({})
const error = ref('')
async function reveal(fid: string) {
  if (fid in shown) { delete shown[fid]; return }
  try { shown[fid] = (await bidApi.reveal(props.projectId, fid)).password ?? '' }
  catch (e) { error.value = e instanceof ApiError ? e.message : '密码读取失败' }
}
function submit(fid: string) { emit('password', fid, passwords[fid] ?? ''); delete passwords[fid]; editing[fid] = false }
</script>
<template>
  <div class="files">
    <div v-for="file in files" :key="file.id" class="file">
      <div class="line"><span class="format">{{ file.ext.toUpperCase() }}</span><span class="filename" :title="file.name">{{ file.name }}</span><span class="state"><span class="dot" :class="file.state" />{{ file.original_deleted ? '原件已清理' : file.state === 'done' ? `已解析${file.page_count ? ` · ${file.page_count} 页` : ''}` : file.state === 'failed' ? '待处理' : '待解析' }}</span><el-button text :disabled="disabled" @click="emit('remove', file.id)">移除</el-button></div>
      <p v-if="rowErrors?.[file.id] || file.error" class="error" role="alert">{{ rowErrors?.[file.id] || file.error }}</p>
      <div v-if="file.ext === 'pdf' && !file.original_deleted" class="password-row">
        <template v-if="file.has_password"><span class="hint">PDF 密码</span><code>{{ file.id in shown ? shown[file.id] : '••••••••' }}</code><el-button text @click="reveal(file.id)">{{ file.id in shown ? '隐藏' : '显示' }}</el-button><el-button text :disabled="disabled" @click="editing[file.id] = !editing[file.id]">更换密码</el-button></template>
        <template v-if="file.needs_password || editing[file.id]"><el-input v-model="passwords[file.id]" type="password" autocomplete="off" aria-label="PDF 打开密码" placeholder="投标方提供的 PDF 密码" :disabled="disabled" @keyup.enter="submit(file.id)" /><el-button :disabled="disabled" @click="submit(file.id)">验证并解析</el-button></template>
      </div>
      <p v-for="warning in file.warnings" :key="warning" class="hint warning">{{ warning }}</p>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>
<style scoped>
.file { padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }.file:last-child { border: 0; }
.line { display: flex; align-items: center; gap: 10px; min-width: 0; }.format { font: var(--si-font-code-sm); color: var(--si-ink-3); flex: none; width: 38px; }
.filename { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }.state { flex: none; font: var(--si-font-meta); color: var(--si-ink-2); }
.dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 6px; background: var(--si-info); }.dot.failed { background: var(--si-warn); }.dot.done { background: var(--si-ok); }
.password-row { display: flex; align-items: center; gap: 10px; padding-left: 48px; flex-wrap: wrap; margin-top: 6px; }.password-row .el-input { width: 240px; }
.error { color: var(--si-danger-ink); margin: 6px 0 0 48px; font: var(--si-font-meta); }.warning { margin: 4px 0 0 48px; }
</style>
