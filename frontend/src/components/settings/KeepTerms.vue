<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminApi, type KeepTerm } from '@/api/admin'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'
import EmptyState from '@/components/EmptyState.vue'
const items = ref<KeepTerm[]>([]), total = ref(0), page = ref(1), q = ref(''), busy = ref(false), error = ref('')
let version = 0
async function load() {
  const current = ++version; busy.value = true; error.value = ''
  try {
    // 每页 20 条，与接口分页一致（V31Q-88）
    const result = await adminApi.keepTerms({q:q.value.trim(),page:page.value})
    if (current !== version) return
    total.value = result.total; items.value = result.items
    if (!items.value.length && total.value && page.value > 1) { page.value = Math.ceil(total.value/20); await load() }
  } catch(e) { if(current === version) error.value = e instanceof ApiError ? e.message : '术语加载失败，请重试' }
  finally { if(current === version) busy.value = false }
}
function search() { page.value=1; void load() }
async function approve(row: KeepTerm) { if(busy.value) return; busy.value=true; try { await adminApi.approveKeepTerm(row.term_hash); ElMessage.success('已认可'); await load() } catch(e) { error.value=e instanceof ApiError ? e.message : '认可失败，请重试' } finally {busy.value=false} }
async function remove(row: KeepTerm) {
  try { await ElMessageBox.confirm('删除后所有人对该词的保留记忆一并删除，无法恢复。','删除术语记忆',{type:'warning',confirmButtonText:'删除',cancelButtonText:'取消'}) } catch { return }
  busy.value=true
  try {await adminApi.removeKeepTerm(row.term_hash); ElMessage.success('已删除'); await load()} catch(e) {error.value=e instanceof ApiError ? e.message : '删除失败，请重试'} finally {busy.value=false}
}
onMounted(load)
</script>
<template>
  <div><div class="filters"><el-input v-model="q" clearable maxlength="100" placeholder="搜索术语" aria-label="搜索术语" @keyup.enter="search" @clear="search" /><el-button @click="search">搜索</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" /><el-table v-loading="busy" :data="items" size="small" max-height="480"><template #empty><EmptyState compact title="没有术语记忆" description="用户在确认页保留原文后，符合条件的词会出现在这里。" /></template><el-table-column prop="term" label="文字" min-width="160" show-overflow-tooltip /><el-table-column prop="users" label="保留人数" width="90" /><el-table-column prop="status" label="状态" width="90" /><el-table-column prop="hit_count" label="命中次数" width="90" /><el-table-column label="最近命中时间" width="170"><template #default="{ row }">{{ row.last_hit_at ? formatDateTime(row.last_hit_at) : '—' }}</template></el-table-column><el-table-column label="操作" width="120"><template #default="{row}"><el-button v-if="row.status !== '已认可'" link type="primary" :disabled="busy" @click="approve(row)">认可</el-button><el-button link type="danger" :disabled="busy" @click="remove(row)">删除</el-button></template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" hide-on-single-page @current-change="load" /></div>
</template>
<style scoped>.filters { margin-bottom: 16px; }.filters :deep(.el-input) { width: 240px; }.el-pagination { margin-top: 12px; justify-content: flex-end; }</style>
