<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SettingSection from '@/components/SettingSection.vue'
import RowMore from '@/components/RowMore.vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCategory } from '@/api/bid'

// 标书分类（V34-B-01a）：全公司统一；已用的分类不能删除，只能停用
const rows = ref<BidCategory[]>([]), loading = ref(true), saving = ref(false), error = ref(''), name = ref('')
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
  if (!error.value) name.value = ''
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
}
function toggle(row: BidCategory, on: boolean) { void run(() => bidApi.patchCategory(row.id, { enabled: on }), on ? '已启用' : '已停用') }
onMounted(() => run(() => bidApi.adminCategories()))
</script>

<template>
  <div class="panel setting-panel">
    <SettingSection title="标书分类" description="新建标书初审项目时必选一个分类，全公司统一。停用的分类不能再选，已使用它的项目保留；已有项目使用的分类不能删除。" stacked>
      <SiNotice v-if="error" :title="error" type="error" :closable="false" class="gap" />
      <ul v-loading="loading" class="cats" aria-label="标书分类">
        <li v-for="row in rows" :key="row.id" class="cat" :class="{ off: !row.enabled }">
          <span class="name">{{ row.name }}</span>
          <span class="hint num used">{{ row.used ? `${row.used} 个项目` : '未使用' }}</span>
          <el-switch :model-value="row.enabled" :disabled="saving" :aria-label="`启用${row.name}`" @change="(on: string | number | boolean) => toggle(row, Boolean(on))" />
          <span class="state hint">{{ row.enabled ? '启用' : '已停用' }}</span>
          <RowMore :disabled="saving">
            <el-dropdown-item @click="rename(row)">改名</el-dropdown-item>
            <el-dropdown-item class="danger-item" :disabled="!!row.used" @click="remove(row)">{{ row.used ? '已使用，不能删除' : '删除' }}</el-dropdown-item>
          </RowMore>
        </li>
      </ul>
      <form class="add" @submit.prevent="add">
        <el-input v-model="name" maxlength="20" placeholder="新分类名称，例如：咨询类" :disabled="saving" />
        <el-button native-type="submit" :disabled="saving || !name.trim()">添加分类</el-button>
      </form>
    </SettingSection>
  </div>
</template>

<style scoped>
.cats { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); max-width: 640px; }
.cat { display: flex; align-items: center; gap: 16px; height: 44px; border-bottom: 1px solid var(--si-hair-faint); }
.name { flex: 1; font: var(--si-font-label); color: var(--si-ink-1); }
.off .name { color: var(--si-ink-4); }
.used { width: 72px; text-align: right; }
.state { width: 44px; }
.add { display: flex; gap: 12px; max-width: 640px; margin-top: 16px; }
.gap { margin-bottom: 12px; }
</style>
