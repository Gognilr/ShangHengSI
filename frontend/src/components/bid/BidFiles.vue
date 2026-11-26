<script setup lang="ts">
import { reactive, ref } from 'vue'
import { Lock, X } from '@lucide/vue'
import { bidApi, formatSize, type BidFile } from '@/api/bid'
import { ApiError } from '@/api/http'
import BidStatus from './BidStatus.vue'

// 文件行：类型徽标 · 名称与大小页数 · 状态 · 移除；需要密码时就地输入，密码默认遮盖、本人可显示
const props = defineProps<{ projectId: string; files: BidFile[]; disabled: boolean; busy?: boolean; rowErrors?: Record<string, string> }>()
const emit = defineEmits<{ password: [fid: string, password: string]; remove: [fid: string] }>()
const passwords = reactive<Record<string, string>>({}), shown = reactive<Record<string, string>>({}), editing = reactive<Record<string, boolean>>({})
const error = ref('')
async function reveal(fid: string) {
  if (fid in shown) { delete shown[fid]; return }
  try { shown[fid] = (await bidApi.reveal(props.projectId, fid)).password ?? '' }
  catch (e) { error.value = e instanceof ApiError ? e.message : '密码读取失败' }
}
function submit(fid: string) {
  if (!passwords[fid]) return
  emit('password', fid, passwords[fid]!); delete passwords[fid]; editing[fid] = false
}
function state(file: BidFile) {
  if (file.original_deleted) return { tone: 'idle' as const, label: '原件已清理' }
  if (file.state === 'done') return { tone: 'ok' as const, label: '已解析' }
  if (file.state === 'failed') return { tone: 'warn' as const, label: file.needs_password ? '需要密码' : '待处理' }
  return props.busy ? { tone: 'run' as const, label: '解析中' } : { tone: 'idle' as const, label: '待解析' }
}
</script>

<template>
  <ul class="bid-files">
    <li v-for="file in files" :key="file.id" class="file">
      <div class="line">
        <span class="badge">{{ file.ext.toUpperCase().slice(0, 4) }}</span>
        <div class="main">
          <span class="filename" :title="file.name">{{ file.name }}</span>
          <span class="sub num">
            {{ formatSize(file.size_bytes) }}<template v-if="file.page_count"> · {{ file.page_count }} 页</template>
            <template v-if="file.has_password && !editing[file.id]">
              · <Lock :size="12" :stroke-width="1.75" class="lock" aria-hidden="true" />密码 <code>{{ file.id in shown ? shown[file.id] : '••••••' }}</code>
              <button type="button" class="link" @click="reveal(file.id)">{{ file.id in shown ? '隐藏' : '显示' }}</button>
              <button type="button" class="link" :disabled="disabled" @click="editing[file.id] = true">更换</button>
            </template>
          </span>
        </div>
        <BidStatus class="state" v-bind="state(file)" />
        <el-button class="remove" text :disabled="disabled" :aria-label="`移除 ${file.name}`" title="移除" @click="emit('remove', file.id)"><X :size="16" :stroke-width="1.75" /></el-button>
      </div>
      <div v-if="file.ext === 'pdf' && !file.original_deleted && (file.needs_password || editing[file.id])" class="detail password">
        <span class="ask" :class="{ wrong: rowErrors?.[file.id] }"><Lock :size="14" :stroke-width="1.75" aria-hidden="true" />{{ rowErrors?.[file.id] || (editing[file.id] ? '输入新的打开密码' : '此 PDF 需要打开密码') }}</span>
        <el-input v-model="passwords[file.id]" type="password" show-password autocomplete="off" aria-label="PDF 打开密码" placeholder="投标方提供的密码" :disabled="disabled" @keyup.enter="submit(file.id)" />
        <el-button :disabled="disabled || !passwords[file.id]" @click="submit(file.id)">验证并解析</el-button>
        <el-button v-if="editing[file.id]" text @click="editing[file.id] = false">取消</el-button>
      </div>
      <p v-else-if="rowErrors?.[file.id] || file.error" class="detail error" role="alert">{{ rowErrors?.[file.id] || file.error }}</p>
      <p v-for="warning in file.warnings" :key="warning" class="detail hint">{{ warning }}</p>
    </li>
    <li v-if="error" class="detail error" role="alert">{{ error }}</li>
  </ul>
</template>

<style scoped>
.bid-files { margin: 0; padding: 0; list-style: none; }
.file { padding: 10px 0; border-top: 1px solid var(--si-hair-faint); }
.file:first-child { border-top: 0; }
.line { display: flex; align-items: center; gap: 12px; min-width: 0; }
.badge { flex: none; box-sizing: border-box; min-width: 34px; height: 30px; padding: 0 4px; display: grid; place-items: center; border: 1px solid var(--si-hair); border-radius: 6px; font: var(--si-font-code-sm); font-size: calc(12px * var(--si-fs)); color: var(--si-ink-3); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.filename { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); color: var(--si-ink-1); }
.sub { display: flex; align-items: center; gap: 4px; font: var(--si-font-caption); color: var(--si-ink-3); }
.sub code { font: var(--si-font-code-sm); color: var(--si-ink-2); }
.lock { margin-left: 2px; }
.link { padding: 0 2px; border: 0; background: none; color: var(--si-blue-ink); font: var(--si-font-caption); cursor: pointer; }
.link:hover { text-decoration: underline; }
.link:disabled { color: var(--si-ink-4); cursor: default; text-decoration: none; }
.state { flex: none; width: 84px; }
.remove { flex: none; color: var(--si-ink-4); }
.remove:hover { color: var(--si-ink-1); }
.detail { margin: 8px 0 0 46px; }
.password { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.password .el-input { width: 220px; }
.ask { display: inline-flex; align-items: center; gap: 6px; font: var(--si-font-meta); color: var(--si-warn-ink); }
.error, .ask.wrong { color: var(--si-danger-ink); font: var(--si-font-meta); }
.hint.detail { margin-top: 4px; }
</style>
