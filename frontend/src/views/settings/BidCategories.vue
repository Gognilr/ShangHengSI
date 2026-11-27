<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import SiBadge from '@/components/ui/SiBadge.vue'
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Folder, FolderOpen, Plus } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCategory } from '@/api/bid'

// 标书分类（V34-B-01a；澄明二版 6.26）：分类在左，计数直接标在列表上，选中分类的详情在右。
// 全公司统一；已用的分类不能删除，只能停用。分类没有补充要求字段，右侧只放启用、改名与删除（V36Q-20）。
const rows = ref<BidCategory[]>([]), loading = ref(true), saving = ref(false), error = ref(''), name = ref('')
const picked = ref<number | null>(null)
const current = computed(() => rows.value.find(r => r.id === picked.value) ?? rows.value[0] ?? null)
const err = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
async function run(action: () => Promise<{ items: BidCategory[] }>, done?: string) {
  saving.value = true; error.value = ''
  try { rows.value = (await action()).items; if (done) ElMessage.success(done) }
  catch (e) { error.value = err(e) }
  finally { saving.value = false; loading.value = false }
}
async function add() {
  const value = name.value.trim()
  if (!value || saving.value) return
  await run(() => bidApi.addCategory(value), '分类已添加')
  if (!error.value) { name.value = ''; picked.value = rows.value[rows.value.length - 1]?.id ?? null }
}
async function rename(row: BidCategory) {
  try {
    const { value } = await ElMessageBox.prompt('最多 20 字', '分类改名', { inputValue: row.name, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: v => (!!v.trim() && v.trim().length <= 20) || '请输入 1～20 字' })
    await run(() => bidApi.patchCategory(row.id, { name: value.trim() }), '已改名')
  } catch { /* 取消 */ }
}
async function remove(row: BidCategory) {
  try { await ElMessageBox.confirm(`删除分类“${row.name}”？`, '删除分类', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  await run(() => bidApi.removeCategory(row.id), '分类已删除')
  picked.value = null
}
function toggle(row: BidCategory, on: boolean) { void run(() => bidApi.patchCategory(row.id, { enabled: on }), on ? '已启用' : '已停用') }
onMounted(() => run(() => bidApi.adminCategories()))
</script>

<template>
  <div class="settings-page cat-page">
    <header class="head"><h2>标书分类</h2><p class="aux">新建标书初审项目时必选一个分类，全公司统一。计数是分类下的项目数；停用的分类不能再选，已使用它的项目保留；已有项目使用的分类不能删除。</p></header>
    <SiNotice v-if="error" :title="error" type="error" :closable="false" class="gap" />
    <div v-loading="loading" class="ctree">
      <div class="card tr2" role="listbox" aria-label="标书分类">
        <button v-for="row in rows" :key="row.id" type="button" role="option" class="tn" :class="{ on: current?.id === row.id, off: !row.enabled }" :aria-selected="current?.id === row.id" @click="picked = row.id">
          <component :is="current?.id === row.id ? FolderOpen : Folder" :size="16" :stroke-width="1.75" /><span class="nm">{{ row.name }}</span><span class="cnt num">{{ row.used ?? 0 }}</span>
        </button>
        <form class="add" @submit.prevent="add">
          <el-input v-model="name" maxlength="20" placeholder="新分类，例如：咨询类" :disabled="saving" />
          <el-button native-type="submit" :disabled="saving || !name.trim()"><template #icon><Plus :size="16" :stroke-width="1.75" /></template>添加</el-button>
        </form>
      </div>
      <div v-if="current" class="card detail">
        <div class="row"><h3>{{ current.name }}</h3><SiBadge :tone="current.enabled ? 'ok' : 'neutral'" dot>{{ current.enabled ? '启用' : '已停用' }}</SiBadge></div>
        <p class="aux">{{ current.used ? `${current.used} 个项目使用` : '还没有项目使用' }}</p>
        <div class="fr"><label :for="`en-${current.id}`">启用</label><div class="fx"><el-switch :id="`en-${current.id}`" :model-value="current.enabled" :disabled="saving" :aria-label="`启用${current.name}`" @change="(on: string | number | boolean) => toggle(current!, Boolean(on))" /><em>停用后新建项目不能再选它。</em></div></div>
        <div class="acts"><el-button :disabled="saving" @click="rename(current)">改名</el-button><el-button class="danger" :disabled="saving || !!current.used" :title="current.used ? '已有项目使用，不能删除' : undefined" @click="remove(current)">{{ current.used ? '已使用，不能删除' : '删除' }}</el-button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head { margin-bottom: 16px; }
.head h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.aux { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.card { background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.ctree { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 16px; align-items: start; }
.tr2 { padding: 10px; }
.tn { display: flex; align-items: center; gap: 8px; width: 100%; height: 36px; padding: 0 10px; border: 0; border-radius: var(--si-radius-control); background: transparent; color: var(--si-ink-2); font: var(--si-font-body); text-align: left; cursor: pointer; }
.tn:hover { background: var(--si-hover-bg); }
.tn.on { background: var(--si-blue-tint); color: var(--si-ink-1); font-weight: 600; }
.tn.off { color: var(--si-ink-4); }
.tn:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cnt { display: inline-flex; align-items: center; height: 20px; padding: 0 7px; border-radius: 99px; background: var(--si-hover-bg); color: var(--si-ink-3); font-size: 12px; }
.add { display: flex; gap: 8px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--si-hair-faint); }
.detail { padding: 20px 22px; }
.row { display: flex; align-items: center; gap: 10px; }
.row h3 { margin: 0; font: var(--si-font-h4); color: var(--si-ink-1); }
.fr { display: grid; grid-template-columns: 140px minmax(0, 360px); column-gap: 20px; margin-top: 20px; }
.fr > label { color: var(--si-ink-2); font: var(--si-font-label); line-height: 22px; }
.fx { display: flex; flex-direction: column; gap: 6px; }
.fx em { color: var(--si-ink-3); font: var(--si-font-caption); font-style: normal; }
.acts { display: flex; gap: 8px; margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--si-hair-faint); }
.danger { color: var(--si-danger-ink); }
@media (max-width: 1100px) { .ctree { grid-template-columns: 1fr; } }
</style>
