<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidState, type BidSettings, type BidSummary } from '@/api/bid'
import { formatDateTimeMinute } from '@/utils/time'
import { useAuthStore } from '@/stores/auth'
import BidStatus from '@/components/bid/BidStatus.vue'
import BidSettingsDialog from '@/components/bid/BidSettingsDialog.vue'

const router = useRouter(), route = useRoute(), auth = useAuthStore()
const items = ref<BidSummary[]>([]), total = ref(0), page = ref(1), loading = ref(true), error = ref('')
const creating = ref(false), dialog = ref(false), dialogError = ref('')
const STEPS = [
  { no: '01', title: '招标文件与要求', desc: '开标前上传招标文件，确认关键参数与要求清单。' },
  { no: '02', title: '投标文件', desc: '按投标人分组拖入；带密码的 PDF 就地输入密码。' },
  { no: '03', title: '初审与报告', desc: '本机比对出不符合项与串标线索，核对后导出报告。' },
]

async function load() {
  loading.value = true; error.value = ''
  try { const data = await bidApi.list(page.value); items.value = data.items; total.value = data.total }
  catch (e) { error.value = e instanceof ApiError ? e.message : '项目列表读取失败' }
  finally { loading.value = false }
}
async function create(body: BidSettings) {
  if (creating.value) return
  creating.value = true; dialogError.value = ''
  try { const project = await bidApi.create(body); dialog.value = false; await router.push(`/bid/${project.id}`) }
  catch (e) { dialogError.value = e instanceof ApiError ? e.message : '新建项目失败，请重试' }
  finally { creating.value = false }
}
function openDialog() { dialogError.value = ''; dialog.value = true }
onMounted(load)
// 全局搜索的“新建标书初审”带 ?new=1 来：直接打开新建对话框，并去掉地址里的参数
watch(() => route.query.new, value => { if (value) { openDialog(); void router.replace({ path: '/bid' }) } }, { immediate: true })
watch(page, load)
</script>

<template>
  <div class="page page--standard bid-home">
    <h1 class="entry-title">标书初审</h1>
    <p class="entry-lede">招标文件与各家投标文件在本机比对，找出不符合项与串标线索；不打分、不推荐中标人。</p>
    <div class="lead-actions">
      <el-button type="primary" @click="openDialog"><Plus :size="16" :stroke-width="1.75" />新建初审项目</el-button>
      <span v-if="auth.can('use.assistant')" class="aside">只是比较几份报价单？用<router-link to="/assistant">工作台的“比价”</router-link></span>
    </div>
    <el-alert v-if="error" class="alert" :title="error" type="error" :closable="false" />

    <section v-if="loading" class="recent" aria-busy="true"><p class="hint" role="status">正在读取项目…</p></section>
    <section v-else-if="items.length" class="recent" aria-label="最近的项目">
      <div class="recent-head"><h2>最近的项目</h2><span class="hint num">共 {{ total }} 个</span></div>
      <router-link v-for="item in items" :key="item.id" :to="`/bid/${item.id}`" class="list-row">
        <span class="name">{{ item.title }}</span>
        <span class="meta">
          <BidStatus class="state" v-bind="bidState(item)" />
          <span class="hint num opening">开标 {{ item.opening_date }}</span>
          <span class="hint num at">{{ formatDateTimeMinute(item.last_activity_at) }}</span>
        </span>
      </router-link>
      <el-pagination v-if="total > 20" v-model:current-page="page" class="pager" :total="total" :page-size="20" layout="prev, pager, next" />
    </section>
    <section v-else class="how" aria-label="一次初审的三步">
      <h2 class="si-overline">一次初审分三步</h2>
      <ol class="steps">
        <li v-for="step in STEPS" :key="step.no"><span class="no">{{ step.no }}</span><strong>{{ step.title }}</strong><p>{{ step.desc }}</p></li>
      </ol>
      <p class="hint">全部材料只在本机处理；选择“模型核对”时，也只发送你确认过的脱敏片段。</p>
    </section>

    <BidSettingsDialog v-model="dialog" title="新建初审项目" confirm-text="创建项目" :busy="creating" :error="dialogError" @submit="create" />
  </div>
</template>

<style scoped>
.bid-home .entry-lede { max-width: none; }
.lead-actions { display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.lead-actions .el-button svg { margin-right: 6px; }
.aside { font: var(--si-font-meta); color: var(--si-ink-3); }
.aside a { margin-left: 2px; }
.alert { margin-top: 20px; }
.recent { margin-top: 40px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; padding-bottom: 10px; border-bottom: 1px solid var(--si-hair-strong); }
.recent-head h2 { margin: 0; font: var(--si-font-h3); }
.list-row .name { flex: 1; }
.meta { flex: none; display: flex; align-items: center; gap: 20px; }
.state { width: 96px; }
.opening { width: 112px; }
.at { width: 116px; text-align: right; }
.pager { margin-top: 16px; }
.how { margin-top: 48px; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); margin: 0 0 16px; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); border-bottom: 1px solid var(--si-hair); }
.steps li { padding: 20px 20px 22px; border-left: 1px solid var(--si-hair-faint); }
.steps li:first-child { border-left: 0; padding-left: 0; }
.no { display: block; margin-bottom: 14px; font: var(--si-font-code-sm); color: var(--si-ink-4); }
.steps strong { font: var(--si-font-h3); color: var(--si-ink-1); }
.steps p { margin: 6px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
</style>
