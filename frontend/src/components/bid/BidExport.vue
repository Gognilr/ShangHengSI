<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FileDown } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCheck, type BidProject } from '@/api/bid'
import { reportApi } from '@/api/report'
import { useAuthStore } from '@/stores/auth'

// 第 4 步：导出问题清单（V35-E-01）。Word 在服务器内存中生成，不留存；这里只说清“会写什么”。
const props = defineProps<{ project: BidProject }>()
const auth = useAuthStore()
const data = ref<BidCheck>(), error = ref(''), busy = ref<'' | 'full' | 'clues'>('')

onMounted(async () => {
  try { data.value = await bidApi.check(props.project.id) }
  catch (e) { error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
})

const facts = computed(() => {
  const c = data.value?.conclusion
  return { severe: c?.severe.length ?? 0, hint: c?.hint.length ?? 0, cases: c?.cases.length ?? 0, dismissed: c?.dismissed.length ?? 0 }
})
const modelRunning = computed(() => ['preparing', 'running'].includes(props.project.model?.state ?? ''))
const canExport = computed(() => auth.can('use.export'))

async function download(scope: 'full' | 'clues') {
  if (busy.value) return
  busy.value = scope
  try { if (await reportApi.bid(props.project.id, scope) === 'downloaded') ElMessage.success('问题清单已下载') }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '导出失败，请稍后重试') }
  finally { busy.value = '' }
}
</script>

<template>
  <section class="export" aria-label="导出">
    <h2 class="si-overline">导出</h2>
    <p class="lede">问题清单（Word，供经办人员内部使用）：严重问题按投标人分节，每条写问题、依据与位置；串标线索附两家原文并排对照；提示另列；附条款清单、文件清单与签章页核对结果。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul v-if="data" class="facts">
      <li><span class="num">{{ facts.severe }}</span>条严重问题，<span class="num">{{ facts.cases }}</span>组串标线索，<span class="num">{{ facts.hint }}</span>条提示</li>
      <li v-if="facts.dismissed"><span class="num">{{ facts.dismissed }}</span>条标为“不是问题”，不写入清单</li>
      <li v-if="modelRunning" class="warn">模型补查还在进行，此时导出的结果不完整</li>
    </ul>
    <p v-else-if="!error" class="hint" role="status">正在读取初审结果…</p>
    <div class="actions">
      <el-button type="primary" :loading="busy === 'full'" :disabled="!canExport || !!busy || !data" @click="download('full')"><FileDown :size="16" :stroke-width="1.75" />导出问题清单</el-button>
      <el-button :loading="busy === 'clues'" :disabled="!canExport || !!busy || !data" @click="download('clues')">只导出串标线索</el-button>
    </div>
    <p v-if="!canExport" class="hint">当前账号没有导出权限，请联系管理员。</p>
    <p class="hint">清单在服务器内存中生成，不保存；投标人用真实名称。</p>
  </section>
</template>

<style scoped>
.export { max-width: 760px; }
.export .si-overline { margin: 0 0 12px; }
.lede { margin: 0 0 20px; font: var(--si-font-body); color: var(--si-ink-2); }
.facts { margin: 0 0 24px; padding: 0; list-style: none; border-top: 1px solid var(--si-hair); }
.facts li { padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-2); }
.facts .num { margin-right: 4px; font-weight: 600; color: var(--si-ink-1); }
.facts .warn { color: var(--si-warn-ink); }
.actions { display: flex; gap: 8px; margin-bottom: 16px; }
.actions .el-button svg { margin-right: 6px; }
.hint { margin: 0 0 8px; font: var(--si-font-meta); color: var(--si-ink-3); }
.error { margin: 0 0 12px; color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
