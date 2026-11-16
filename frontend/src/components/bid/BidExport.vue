<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FileDown } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCheck, type BidProject } from '@/api/bid'
import { reportApi } from '@/api/report'
import { useAuthStore } from '@/stores/auth'

// 第 4 步：导出（bid.md 2.5）。Word 在服务器内存中生成，不留存；这里只说清“会写什么”和“还有什么没核对”。
const props = defineProps<{ project: BidProject }>()
const auth = useAuthStore()
const data = ref<BidCheck>(), error = ref(''), busy = ref<'' | 'full' | 'clues'>('')

onMounted(async () => {
  try { data.value = await bidApi.check(props.project.id) }
  catch (e) { error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
})

const facts = computed(() => {
  const items = data.value?.items ?? []
  const live = items.filter(i => i.review_status !== 'excluded' && i.kind !== 'normal_dup')
  const veto = live.filter(i => i.kind === 'issue' && i.level === 'veto')
  return {
    veto: veto.length, vetoVerified: veto.filter(i => i.review_status === 'verified').length,
    clues: live.filter(i => i.kind === 'clue').length, issues: live.filter(i => i.kind === 'issue').length,
    pending: live.filter(i => i.review_status === 'pending').length,
    excluded: items.filter(i => i.review_status === 'excluded' && i.kind !== 'normal_dup').length,
  }
})
const modelRunning = computed(() => ['preparing', 'running'].includes(props.project.model?.state ?? ''))
const canExport = computed(() => auth.can('use.export'))

async function download(scope: 'full' | 'clues') {
  if (busy.value) return
  busy.value = scope
  try { if (await reportApi.bid(props.project.id, scope) === 'downloaded') ElMessage.success('报告已下载') }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '导出失败，请稍后重试') }
  finally { busy.value = '' }
}
</script>

<template>
  <section class="export" aria-label="导出">
    <h2 class="si-overline">导出</h2>
    <p class="lede">《初审疑点报告》（Word）交给评标委员会：总览、要求响应矩阵、各投标人问题、串标线索（两家原文并排对照）、报价分析，附要求清单、文件清单与签章页核对结果，末尾留初审人 / 复核人签字栏。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul v-if="data" class="facts">
      <li><span class="num">{{ facts.veto }}</span>项疑似否决项，其中 <span class="num">{{ facts.vetoVerified }}</span> 项已人工核实<template v-if="facts.veto > facts.vetoVerified">；未核实的在报告里保持“疑似”口吻</template></li>
      <li><span class="num">{{ facts.clues }}</span>条串标线索，<span class="num">{{ facts.issues }}</span>条问题</li>
      <li :class="{ warn: facts.pending }"><span class="num">{{ facts.pending }}</span>条问题与线索待核对，报告里如实标为“待核对”</li>
      <li v-if="facts.excluded"><span class="num">{{ facts.excluded }}</span>条已排除，不写入报告正文</li>
      <li v-if="modelRunning" class="warn">模型核对还在进行，此时导出的“模型判断”结果不完整</li>
    </ul>
    <p v-else-if="!error" class="hint" role="status">正在读取初审结果…</p>
    <div class="actions">
      <el-button type="primary" :loading="busy === 'full'" :disabled="!canExport || !!busy || !data" @click="download('full')"><FileDown :size="16" :stroke-width="1.75" />导出初审疑点报告</el-button>
      <el-button :loading="busy === 'clues'" :disabled="!canExport || !!busy || !data" @click="download('clues')">只导出串标线索</el-button>
    </div>
    <p v-if="!canExport" class="hint">当前账号没有导出权限，请联系管理员。</p>
    <p class="hint">报告在服务器内存中生成，不保存；投标人用真实名称。需要改动核对结果时，回到“初审结果”修改后再导出。</p>
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
