<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import { adminRedactionApi, type TermRow } from '@/api/admin'
import { ApiError } from '@/api/http'
import { formatDateTime } from '@/utils/time'
import { Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback)

// ---- ① 数量脱敏 ----
const maskQuantity = ref(false)
const maskLoading = ref(false)
const maskError = ref('')

async function loadMask() {
  maskLoading.value = true
  maskError.value = ''
  try {
    maskQuantity.value = (await adminRedactionApi.getSetting()).mask_quantity
  } catch (e) {
    maskError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    maskLoading.value = false
  }
}

async function toggleMask(value: string | number | boolean) {
  const target = value === true
  maskLoading.value = true
  try {
    maskQuantity.value = (await adminRedactionApi.saveSetting(target)).mask_quantity
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
    await loadMask()
  } finally {
    maskLoading.value = false
  }
}

// ---- ② 自定义敏感词 ----
const custom = ref<TermRow[]>([])
const customLoading = ref(false)
const customError = ref('')
const newTerm = ref('')
const adding = ref(false)

async function loadCustom() {
  customLoading.value = true
  customError.value = ''
  try {
    custom.value = (await adminRedactionApi.terms('custom', '', 1)).items
  } catch (e) {
    customError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    customLoading.value = false
  }
}

async function addTerm() {
  const term = newTerm.value.trim()
  if (term.length < 2 || term.length > 100) {
    ElMessage.error('敏感词须为 2–100 个字符')
    return
  }
  adding.value = true
  try {
    await adminRedactionApi.addTerm(term)
    newTerm.value = ''
    ElMessage.success('已添加，之后新解析的文件会替换该敏感词')
    await loadCustom()
  } catch (e) {
    ElMessage.error(errText(e, '添加失败，请稍后重试'))
  } finally {
    adding.value = false
  }
}

async function toggleTerm(row: TermRow, value: string | number | boolean) {
  try {
    await adminRedactionApi.setTermEnabled(row.id, value === true)
    ElMessage.success(value === true ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error(errText(e, '保存失败，请稍后重试'))
  }
  await loadCustom()
}

async function removeTerm(row: TermRow, reload: () => Promise<void>) {
  try {
    await ElMessageBox.confirm(
      `将删除「${row.term}」。已解析的任务不受影响，删除后无法恢复。`,
      '删除词条',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await adminRedactionApi.removeTerm(row.id)
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error(errText(e, '删除失败，请稍后重试'))
  }
  await reload()
  if (row.is_our_party) await loadOurParties()
}

// ---- ③ 已记住的名称 ----
const learned = ref<TermRow[]>([])
const learnedTotal = ref(0)
const learnedPage = ref(1)
const learnedSize = ref(20)
const learnedQuery = ref('')
const learnedLoading = ref(false)
const learnedError = ref('')

async function loadLearned() {
  learnedLoading.value = true
  learnedError.value = ''
  try {
    const res = await adminRedactionApi.terms('learned', learnedQuery.value.trim(), learnedPage.value)
    learned.value = res.items
    learnedTotal.value = res.total
    learnedSize.value = res.page_size
    // 删除后当前页已超出范围时回到最后一页
    if (!res.items.length && res.total > 0 && learnedPage.value > 1) {
      learnedPage.value = Math.ceil(res.total / res.page_size)
      await loadLearned()
    }
  } catch (e) {
    learnedError.value = `加载失败：${errText(e, '请刷新页面')}`
  } finally {
    learnedLoading.value = false
  }
}

function searchLearned() {
  learnedPage.value = 1
  void loadLearned()
}

// ---- ④ 我方主体 ----
const ourParties = ref<TermRow[]>([])
const ourLoading = ref(false)
const ourAdding = ref(false)
const ourError = ref('')
const ourName = ref('')
const ourRemoving = ref('')
async function loadOurParties() {
  ourLoading.value = true
  ourError.value = ''
  try { ourParties.value = (await adminRedactionApi.ourParties()).items }
  catch (e) { ourError.value = errText(e, '我方主体加载失败，请刷新页面') }
  finally { ourLoading.value = false }
}
async function addOurParty() {
  if (ourAdding.value) return
  const name = ourName.value.trim()
  if (Array.from(name).length < 2 || Array.from(name).length > 100) {
    ourError.value = '我方主体名称须为 2–100 个字符'
    return
  }
  ourAdding.value = true
  ourError.value = ''
  try {
    await adminRedactionApi.addOurParty(name)
    ourName.value = ''
    await Promise.all([loadOurParties(), loadLearned()])
    ElMessage.success('已添加我方主体')
  } catch (e) { ourError.value = errText(e, '添加失败，请重试') }
  finally { ourAdding.value = false }
}
async function removeOurParty(row: TermRow) {
  if (ourRemoving.value) return
  try {
    await ElMessageBox.confirm(`将删除「${row.term}」的已记住名称及我方预置。已解析任务不受影响，删除后无法恢复。`,
      '删除我方主体', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  ourRemoving.value = row.id
  ourError.value = ''
  try {
    await adminRedactionApi.removeOurParty(row.id)
    await Promise.all([loadOurParties(), loadLearned()])
    ElMessage.success('已删除我方主体')
  } catch (e) { ourError.value = errText(e, '删除失败，请重试') }
  finally { ourRemoving.value = '' }
}

onMounted(() => {
  void loadMask()
  void loadCustom()
  void loadLearned()
  void loadOurParties()
})
</script>

<template>
  <div class="settings-page">
    <PageHeader title="脱敏规则" description="新增、停用、删除只影响之后解析的文件；已解析的任务不会自动重算" />
    <section class="panel">
      <el-alert v-if="maskError" type="error" :closable="false" show-icon :title="maskError" />
      <div class="quantity-row"><div><h3>数量脱敏</h3><span class="hint">开启后数量也会替换为代号，只影响之后解析的文件。</span></div><el-switch :model-value="maskQuantity" :loading="maskLoading" @change="toggleMask" /></div>
    </section>

    <section class="panel">
      <h3 class="first">自定义敏感词</h3>
      <div class="filters">
        <el-input v-model="newTerm" maxlength="100" placeholder="输入需要替换的敏感词（2–100 个字符）" class="term-input" @keyup.enter="addTerm" />
        <el-button type="primary" :loading="adding" @click="addTerm">添加</el-button>
      </div>
      <div class="hint tip">新增、停用、删除只影响之后解析的文件；已解析的任务不会自动重算。</div>
      <el-alert v-if="customError" type="error" :closable="false" show-icon :title="customError" />
      <el-table v-loading="customLoading" :data="custom" size="small" empty-text="尚未添加自定义敏感词">
        <el-table-column prop="term" label="敏感词" min-width="260" />
        <el-table-column label="记录人" width="120">
          <template #default="{ row }">{{ row.created_by_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v: string | number | boolean) => toggleTerm(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="添加时间" width="170" class-name="num">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeTerm(row, loadCustom)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section class="panel">
      <div class="section-heading"><h3 class="first">已记住的名称</h3>
      <div class="filters">
        <el-input v-model="learnedQuery" clearable placeholder="搜索名称" :prefix-icon="Search" class="learned-query" @keyup.enter="searchLearned" @clear="searchLearned" />
        <el-button @click="searchLearned">搜索</el-button>
      </div>
      </div>
      <el-alert v-if="learnedError" type="error" :closable="false" show-icon :title="learnedError" />
      <el-table v-loading="learnedLoading" :data="learned" size="small" empty-text="没有已记住的名称">
        <el-table-column prop="term" label="名称" min-width="240" />
        <el-table-column label="类别" width="80">
          <template #default="{ row }">{{ row.category === 'PERSON' ? '人名' : '公司' }}</template>
        </el-table-column>
        <el-table-column label="是否我方" width="90">
          <template #default="{ row }">{{ row.is_our_party ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="记录人" width="120">
          <template #default="{ row }">{{ row.created_by_name ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="记录时间" width="170" class-name="num">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="hit_count" label="命中次数" width="90" class-name="num" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeTerm(row, loadLearned)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="learnedTotal > learnedSize"
        v-model:current-page="learnedPage"
        class="pager"
        :page-size="learnedSize"
        :total="learnedTotal"
        layout="total, prev, pager, next"
        @current-change="loadLearned"
      />
    </section>
    <section class="panel">
      <h3>我方主体</h3>
      <p class="hint tip">预置本公司的完整名称，之后新解析的文件出现该名称时可预选我方主体。</p>
      <div class="filters">
        <el-input v-model="ourName" maxlength="100" class="term-input" placeholder="输入我方完整公司名称" aria-label="我方主体名称" :disabled="ourAdding" @keyup.enter="addOurParty" />
        <el-button type="primary" :loading="ourAdding" @click="addOurParty">添加我方主体</el-button>
      </div>
      <el-alert v-if="ourError" type="error" show-icon :closable="false" :title="ourError" class="gap" />
      <el-table v-loading="ourLoading" :data="ourParties" size="small" empty-text="尚未预置我方主体">
        <el-table-column prop="term" label="名称" min-width="260" />
        <el-table-column label="记录人" width="120"><template #default="{ row }">{{ row.created_by_name ?? '—' }}</template></el-table-column>
        <el-table-column label="记录时间" width="170" class-name="num"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="danger" :loading="ourRemoving === row.id" :disabled="!!ourRemoving" @click="removeOurParty(row)">删除</el-button></template></el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
h3 {
  margin: 0 0 16px;
  font: var(--si-font-h2);
}
.row {
  display: flex;
  gap: 12px;
  align-items: center;
}
.tip {
  margin: 0 0 16px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.quantity-row, .section-heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.section-heading { margin-bottom: 16px; }
.section-heading h3, .section-heading .filters { margin: 0; }
.term-input { width: 360px; }
.learned-query { width: 240px; }
.filters { flex-wrap: wrap; }
</style>
