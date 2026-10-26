<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadUserFile } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { ApiError } from '@/api/http'
import { lawsApi, type LawRow, type LawImport } from '@/api/laws'
import { LAW_STATUS, LAW_IMPORT_STATUS, LAW_IMPORT_KIND, LAW_SOURCE, LAW_FIELD, toOptions } from '@/utils/labels'
import { formatDate, formatDateTime } from '@/utils/time'

const rows = ref<LawRow[]>([])
const records = ref<LawImport[]>([])
const counts = ref<Record<string, number>>({})
const total = ref(0)
const filter = reactive({ q: '', category: '', status: '', page: 1, page_size: 20 })
const enabled = ref(false)
const settingsLoaded = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const detail = ref<LawImport | null>(null)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const uploadOpen = ref(false)
const kind = ref<'catalog' | 'fulltext'>('catalog')
const status = ref('')
const files = ref<UploadUserFile[]>([])
const uploadError = ref('')
const pending = computed(() => records.value.some(r => ['parsing', 'pending'].includes(r.status)))
const fulltextCount = computed(() => rows.value.filter(r => r.has_fulltext).length)
const err = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
let timer: ReturnType<typeof setInterval> | null = null
let active = true
let reloadQueued = false

async function load(silent = false) {
  if (loading.value) { reloadQueued = true; return }
  loading.value = true
  if (!silent) error.value = ''
  try {
    const [list, imports, settings] = await Promise.all([lawsApi.list(filter), lawsApi.imports(), lawsApi.settings()])
    if (!active) return
    rows.value = list.items; total.value = list.total; counts.value = list.counts
    records.value = imports.items; enabled.value = settings.refs_enabled; settingsLoaded.value = true; error.value = ''
    if (detailOpen.value && detail.value?.status === 'parsing') await loadDetail(detail.value.id)
  } catch (e) { if (!silent) error.value = `加载失败：${err(e)}。请刷新页面后重试。` }
  finally {
    loading.value = false
    if (reloadQueued && active) { reloadQueued = false; void load() }
  }
}
async function search() { filter.page = 1; await load() }
async function saveSetting(value: string | number | boolean) {
  saving.value = true
  try { enabled.value = (await lawsApi.saveSettings(Boolean(value))).refs_enabled; ElMessage.success('已保存') }
  catch (e) { enabled.value = !Boolean(value); ElMessage.error(err(e)) }
  finally { saving.value = false }
}
async function editReplacement(row: LawRow) {
  try {
    const answer = await ElMessageBox.prompt('填写现行规定的标题，留空可清除。', '编辑替代法规', {
      inputValue: row.replaced_by ?? '', inputValidator: v => v.length <= 300 || '最多 300 字', confirmButtonText: '保存', cancelButtonText: '取消',
    })
    await lawsApi.update(row.id, answer.value); ElMessage.success('已保存'); await load()
  } catch (e) { if (e instanceof Error) ElMessage.error(err(e)) }
}
function openUpload(value: 'catalog' | 'fulltext') {
  kind.value = value; status.value = ''; files.value = []; uploadError.value = ''; uploadOpen.value = true
}
async function upload() {
  const selected = files.value.flatMap(f => f.raw ? [f.raw] : [])
  if (!selected.length || (kind.value === 'catalog' && !status.value)) { uploadError.value = '请选择文件和目录时效性'; return }
  if (kind.value === 'fulltext' && selected.length !== 1) { uploadError.value = '请选择一个全文 ZIP 压缩包'; return }
  if (kind.value === 'catalog' && selected.some(f => f.size > 5 * 1024 ** 2)) { uploadError.value = '每个目录文件最多 5 MB'; return }
  saving.value = true; uploadError.value = ''
  try {
    const result = await lawsApi.upload(kind.value, status.value, selected)
    uploadOpen.value = false; ElMessage.success('文件已上传，解析后请确认差异'); await load(); await loadDetail(result.id)
  } catch (e) { uploadError.value = err(e) }
  finally { saving.value = false }
}
async function loadDetail(id: string) {
  detailOpen.value = true; detailLoading.value = true; detailError.value = ''
  try { detail.value = await lawsApi.detail(id) }
  catch (e) { detailError.value = err(e) }
  finally { detailLoading.value = false }
}
async function decide(action: 'apply' | 'discard') {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(action === 'apply' ? '确认将所列法规目录和全文差异写入法规库？' : '放弃本次更新及暂存全文？此操作无法恢复。',
      action === 'apply' ? '确认生效' : '放弃更新', { type: 'warning', confirmButtonText: action === 'apply' ? '确认生效' : '放弃', cancelButtonText: '取消' })
  } catch { return }
  saving.value = true
  try { detail.value = await lawsApi[action](detail.value.id); ElMessage.success(action === 'apply' ? '更新已生效' : '已放弃更新'); await load() }
  catch (e) { detailError.value = err(e) }
  finally { saving.value = false }
}
function changeValue(key: string, value: unknown) {
  if (value == null || value === '') return '（空）'
  if (key === 'status') return LAW_STATUS[String(value)] ?? String(value)
  if (key === 'effective_on') return formatDate(String(value))
  return String(value)
}
onMounted(() => {
  void load()
  timer = setInterval(() => { if (pending.value && !saving.value) void load(true) }, 3000)
})
onBeforeUnmount(() => { active = false; if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <PageHeader title="法规库" description="导入官方法规目录与全文，预览差异并确认生效。" />
    <section class="panel">
      <h2>审核时附带相关法条</h2>
      <el-switch :model-value="enabled" :disabled="saving || loading || !settingsLoaded" aria-label="审核时附带相关法条" @change="saveSetting" />
      <p class="hint">开启后每次审核约多 5 千个 token，默认关闭，评测确认效果后再开启。</p>
    </section>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="gap" />
    <section v-loading="loading" class="panel">
      <div class="counts">
        <span v-for="o in toOptions(LAW_STATUS)" :key="o.value">{{ o.label }} <b class="num">{{ counts[o.value] ?? 0 }}</b></span>
        <span>总数 <b class="num">{{ counts.total ?? 0 }}</b></span><span>本页有全文 <b class="num">{{ fulltextCount }}</b></span>
      </div>
      <div class="filters">
        <el-input v-model="filter.q" clearable placeholder="搜索法规标题" aria-label="搜索法规标题" class="search" @keyup.enter="search" />
        <el-input v-model="filter.category" clearable placeholder="全部分类" aria-label="法规分类" class="category" @keyup.enter="search" />
        <el-select v-model="filter.status" clearable placeholder="全部时效性" aria-label="法规时效性" class="category" @change="search"><el-option v-for="o in toOptions(LAW_STATUS)" :key="o.value" :value="o.value" :label="o.label" /></el-select>
        <el-button @click="search">查询</el-button>
      </div>
      <el-table :data="rows" empty-text="没有符合条件的法规，可导入官方导出的目录">
        <el-table-column prop="title" label="法规标题" min-width="180" />
        <el-table-column prop="category" label="分类" min-width="80" />
        <el-table-column label="时效性" width="80"><template #default="{ row }">{{ LAW_STATUS[row.status] }}</template></el-table-column>
        <el-table-column label="公布日期" width="100"><template #default="{ row }">{{ formatDate(row.published_on) }}</template></el-table-column>
        <el-table-column label="施行日期" width="100"><template #default="{ row }">{{ formatDate(row.effective_on) }}</template></el-table-column>
        <el-table-column label="全文" width="60"><template #default="{ row }">{{ row.has_fulltext ? '有' : '无' }}</template></el-table-column>
        <el-table-column label="替代法规" min-width="120"><template #default="{ row }">{{ row.replaced_by || '—' }}</template></el-table-column>
        <el-table-column label="来源" width="80"><template #default="{ row }">{{ LAW_SOURCE[row.source] }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="editReplacement(row)">编辑替代</el-button></template></el-table-column>
      </el-table>
      <div class="pagination"><span>共 {{ total }} 条</span><el-pagination v-model:current-page="filter.page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="load()" /></div>
    </section>
    <section class="panel">
      <div class="section-head"><h2>法规库更新记录</h2><el-button :disabled="pending || saving" @click="openUpload('catalog')">导入目录</el-button><el-button :disabled="pending || saving" @click="openUpload('fulltext')">导入全文</el-button></div>
      <p v-if="pending" class="hint">有一项更新正在解析或等待确认，请处理后再导入。</p>
      <el-table :data="records" empty-text="尚无导入或同步记录">
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="类型" min-width="100"><template #default="{ row }">{{ LAW_IMPORT_KIND[row.kind] }}</template></el-table-column>
        <el-table-column label="状态" min-width="100"><template #default="{ row }">{{ LAW_IMPORT_STATUS[row.status] }}</template></el-table-column>
        <el-table-column prop="added" label="新增" width="72" /><el-table-column prop="changed" label="变化" width="72" /><el-table-column prop="skipped" label="跳过" width="72" />
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="loadDetail(row.id)">查看差异</el-button></template></el-table-column>
      </el-table>
    </section>
    <el-dialog v-model="uploadOpen" :title="kind === 'catalog' ? '导入法规目录' : '导入法规全文'" width="640px" :close-on-click-modal="!saving">
      <el-alert v-if="uploadError" :title="uploadError" type="error" :closable="false" class="gap" />
      <el-form label-position="top">
        <el-form-item v-if="kind === 'catalog'" label="目录时效性（必选）"><el-select v-model="status" placeholder="请选择时效性"><el-option v-for="o in toOptions(LAW_STATUS)" :key="o.value" :value="o.value" :label="o.label" /></el-select></el-form-item>
        <p class="hint">{{ kind === 'catalog' ? '请在官网按时效性筛选后导出，一次只导入一种时效性的文件，可多选文件' : '建议先导入常用的有效法律和司法解释全文；全部有效法规可在测试虚拟机评测后分批导入。ZIP 内文件名为法规标题_公布日期YYYYMMDD.docx 或 .doc。' }}</p>
        <el-upload v-model:file-list="files" :auto-upload="false" :multiple="kind === 'catalog'" :limit="kind === 'catalog' ? 10 : 1" :accept="kind === 'catalog' ? '.xls,.xlsx' : '.zip'" :disabled="saving" @exceed="ElMessage.error('文件数量超过本次导入限制')"><el-button>选择文件</el-button></el-upload>
      </el-form>
      <template #footer><el-button :disabled="saving" @click="uploadOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="upload">上传并预览差异</el-button></template>
    </el-dialog>
    <el-dialog v-model="detailOpen" title="法规库更新差异" width="640px" :close-on-click-modal="!saving">
      <div v-loading="detailLoading">
        <el-alert v-if="detailError || detail?.error" :title="detailError || detail?.error || ''" type="error" :closable="false" />
        <template v-if="detail">
          <p>{{ LAW_IMPORT_KIND[detail.kind] }} · {{ LAW_IMPORT_STATUS[detail.status] }} · {{ formatDateTime(detail.created_at) }}</p>
          <el-alert v-if="detail.status === 'parsing'" title="正在解析，请稍候；可关闭窗口，稍后查看。" type="info" :closable="false" />
          <p>新增 {{ detail.added }}，变化 {{ detail.changed }}，未变 {{ detail.unchanged }}，跳过 {{ detail.skipped }}</p>
          <template v-if="detail.diff">
            <h3>新增法规</h3><p v-for="(item, i) in detail.diff.added" :key="i">{{ item.title }} · {{ formatDate(item.published_on) }} · {{ LAW_STATUS[item.status ?? ''] }}</p>
            <h3>变化</h3><div v-for="(item, i) in detail.diff.changed" :key="i"><b>{{ item.title }}</b><p v-for="(values, key) in item.changes" :key="key">{{ LAW_FIELD[key] ?? key }}：{{ changeValue(key, values[0]) }} → {{ changeValue(key, values[1]) }}</p></div>
            <h3>匹配的全文</h3><p v-for="(item, i) in [...(detail.diff.matched ?? []), ...(detail.diff.fulltext ?? [])]" :key="i">{{ item.title }} · {{ item.articles }} 条{{ item.replace ? '（替换已有全文）' : '' }}</p>
            <h3>跳过与未匹配</h3><p v-for="(item, i) in detail.diff.skipped" :key="`s${i}`">第 {{ item.file }} 个文件，第 {{ item.row }} 行：{{ item.reason }}</p>
            <p v-for="(item, i) in detail.diff.unmatched" :key="`u${i}`">{{ item.file }}：{{ item.reason }}</p>
            <p v-for="(item, i) in detail.diff.fulltext_failed" :key="`f${i}`">{{ item.title }}：{{ item.reason }}</p>
            <p v-if="detail.diff.duplicates" class="hint">重复条目 {{ detail.diff.duplicates }} 条，仅取首条。</p>
          </template>
        </template>
      </div>
      <template #footer><el-button :disabled="saving" @click="detailOpen = false">关闭</el-button><el-button v-if="detail && ['pending', 'failed'].includes(detail.status)" :loading="saving" @click="decide('discard')">放弃更新</el-button><el-button v-if="detail?.status === 'pending'" type="primary" :loading="saving" @click="decide('apply')">确认生效</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
h2 { font: var(--si-font-h2); margin: 0 0 16px; }
h3 { font: var(--si-font-h4); margin: 24px 0 12px; }
p { overflow-wrap: anywhere; white-space: pre-wrap; }
.counts, .section-head, .pagination { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; }
.counts { margin-bottom: 24px; color: var(--el-text-color-secondary); }
.section-head h2 { margin: 0 auto 0 0; }
.section-head { margin-bottom: 16px; }
.filters { flex-wrap: wrap; }
.search { width: 240px; }
.category { width: 140px; }
.pagination { justify-content: space-between; margin-top: 16px; }
</style>
