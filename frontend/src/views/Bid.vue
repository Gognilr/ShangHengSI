<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { bidApi, bidStageLabel, type BidSettings, type BidSummary } from '@/api/bid'
import { formatDateTimeMinute } from '@/utils/time'
import { useAuthStore } from '@/stores/auth'

const router = useRouter(), auth = useAuthStore()
const items = ref<BidSummary[]>([]), total = ref(0), page = ref(1), loading = ref(true), error = ref('')
const creating = ref(false), dialog = ref(false)
const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
const today = ['year', 'month', 'day'].map(k => parts.find(p => p.type === k)?.value).join('-')
const form = reactive<BidSettings>({ title: '', opening_date: today, policy: 'local_only' })
async function load() {
  loading.value = true; error.value = ''
  try { const data = await bidApi.list(page.value); items.value = data.items; total.value = data.total }
  catch (e) { error.value = e instanceof ApiError ? e.message : '项目列表读取失败' }
  finally { loading.value = false }
}
async function create() {
  if (!form.title.trim() || !form.opening_date || creating.value) return
  creating.value = true; error.value = ''
  try { const project = await bidApi.create(form); await router.push(`/bid/${project.id}`) }
  catch (e) { error.value = e instanceof ApiError ? e.message : '新建项目失败' }
  finally { creating.value = false }
}
onMounted(load)
watch(page, load)
</script>

<template>
  <div class="page page--standard">
    <h1 class="entry-title">标书初审</h1>
    <p class="entry-lede">招标文件与各家投标文件在本机比对，找出不符合项与串标线索；不打分、不推荐。</p>
    <div class="actions"><el-button type="primary" @click="dialog = true">新建初审项目</el-button><span v-if="auth.can('use.assistant')" class="hint">只是比较几份报价单？用<router-link to="/assistant">工作台的“比价”</router-link></span></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <section class="recent" aria-label="最近的项目">
      <h2>最近的项目</h2>
      <p v-if="loading" class="hint" role="status">正在读取项目…</p>
      <p v-else-if="!items.length" class="hint">还没有初审项目。先加入招标文件，再按投标人整理材料。</p>
      <router-link v-for="item in items" :key="item.id" :to="`/bid/${item.id}`" class="list-row">
        <span class="name">{{ item.title }}</span><span class="state">{{ item.status === 'parsing' ? '解析中' : item.status === 'parse_failed' ? '文件待处理' : bidStageLabel[item.stage] }}</span><span class="at num">{{ formatDateTimeMinute(item.last_activity_at) }}</span>
      </router-link>
      <el-pagination v-if="total > 20" v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" />
    </section>
    <el-dialog v-model="dialog" title="新建初审项目" width="520px" :close-on-click-modal="!creating">
      <el-form label-position="top" @submit.prevent="create">
        <el-form-item label="项目名称"><el-input v-model="form.title" maxlength="255" placeholder="填写本次招标项目的名称" /></el-form-item>
        <el-form-item label="开标日期"><el-date-picker v-model="form.opening_date" value-format="YYYY-MM-DD" :clearable="false" /></el-form-item>
        <el-form-item label="外发策略"><el-radio-group v-model="form.policy"><el-radio value="local_only">只做本地检查</el-radio><el-radio value="ai">确认脱敏片段后由模型核对</el-radio></el-radio-group></el-form-item>
      </el-form>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><el-button :disabled="creating" @click="dialog = false">取消</el-button><el-button type="primary" :loading="creating" :disabled="!form.title.trim() || !form.opening_date" @click="create">创建项目</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.actions { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }
.recent { margin-top: 40px; }
h2 { font: var(--si-font-overline); color: var(--si-ink-3); border-bottom: 1px solid var(--si-hair-strong); padding-bottom: 12px; }
.name { flex: 1; }.state { flex: none; width: 100px; color: var(--si-ink-2); font: var(--si-font-meta); }
.at { flex: none; color: var(--si-ink-3); font: var(--si-font-meta); }
.error { color: var(--si-danger-ink); }
</style>
