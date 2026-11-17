<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChevronDown, ChevronRight, Folder, FolderPlus, Inbox, Layers, Plus, Search } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidStageLabel, bidState, type BidCategory, type BidFolderTree, type BidListQuery, type BidSettings, type BidStage, type BidSummary } from '@/api/bid'
import { formatDateTimeMinute } from '@/utils/time'
import { useAuthStore } from '@/stores/auth'
import BidStatus from '@/components/bid/BidStatus.vue'
import BidSettingsDialog from '@/components/bid/BidSettingsDialog.vue'
import RowMore from '@/components/RowMore.vue'
import { folderOptions, folderPath, type FolderNode } from '@/components/bid/folders'

const router = useRouter(), route = useRoute(), auth = useAuthStore()
const items = ref<BidSummary[]>([]), total = ref(0), loading = ref(true), error = ref(''), ready = ref(false)
const tree = ref<BidFolderTree>({ folders: [], all: 0, unfiled: 0 }), categories = ref<BidCategory[]>([])
const creating = ref(false), dialog = ref(false), dialogError = ref('')
const STEPS = [
  { no: '01', title: '招标文件与要求', desc: '开标前上传招标文件，确认关键参数与要求清单。' },
  { no: '02', title: '投标文件', desc: '按投标人分组拖入；带密码的 PDF 就地输入密码。' },
  { no: '03', title: '初审与报告', desc: '本机比对出不符合项与串标线索，核对后导出报告。' },
]
const STAGES = Object.entries(bidStageLabel) as [BidStage, string][]

// 筛选条件放在地址里，从项目返回时还原（V34-B-01a）
const str = (v: unknown) => typeof v === 'string' ? v : ''
const readFilters = () => ({
  q: str(route.query.q), folder: str(route.query.folder), category: str(route.query.category),
  stage: str(route.query.stage) as BidStage | '', dates: (str(route.query.from) && str(route.query.to) ? [str(route.query.from), str(route.query.to)] : null) as [string, string] | null,
  page: Number(route.query.page) || 1,
})
const filters = reactive(readFilters())
const keyword = ref(filters.q)
const query = computed<BidListQuery>(() => ({
  page: filters.page, q: filters.q || undefined, folder: filters.folder || undefined, category: filters.category ? Number(filters.category) : undefined,
  stage: filters.stage || undefined, from: filters.dates?.[0], to: filters.dates?.[1],
}))
const narrowed = computed(() => !!(filters.q || filters.category || filters.stage || filters.dates))
const empty = computed(() => ready.value && !loading.value && !narrowed.value && !filters.folder && !tree.value.all && !tree.value.folders.length)
const nodes = computed(() => folderOptions(tree.value.folders))
const collapsed = reactive(new Set<string>())
const flat = computed(() => { const out: FolderNode[] = []; const walk = (ns: FolderNode[]) => ns.forEach(n => { out.push(n); if (!collapsed.has(n.value)) walk(n.children) }); walk(nodes.value); return out })
const currentName = computed(() => filters.folder === 'none' ? '未归档' : filters.folder ? folderPath(tree.value.folders, filters.folder) : '全部项目')
const categoryName = (id: number | null) => categories.value.find(c => c.id === id)?.name ?? ''

let generation = 0
async function load() {
  const current = ++generation
  loading.value = true; error.value = ''
  try {
    const { q, category, stage, from, to } = query.value
    const [data, folders] = await Promise.all([bidApi.list(query.value), bidApi.folders({ q, category, stage, from, to })])
    if (current === generation) { items.value = data.items; total.value = data.total; tree.value = folders }
  } catch (e) {
    if (current !== generation) return
    // 地址里的文件夹已删除：回到全部项目
    if (e instanceof ApiError && e.status === 404 && filters.folder) { filters.folder = ''; return }
    error.value = e instanceof ApiError ? e.message : '项目列表读取失败'
  } finally { if (current === generation) loading.value = false }
}
watch(query, value => {
  const next = Object.fromEntries(Object.entries(value).filter(([k, v]) => v !== undefined && !(k === 'page' && v === 1)).map(([k, v]) => [k, String(v)]))
  if (Object.keys(next).length !== Object.keys(route.query).length || Object.entries(next).some(([k, v]) => route.query[k] !== v)) void router.replace({ query: next })
}, { deep: true })
// 地址是查询来源；浏览器前进 / 后退同样还原文件夹、控件和右侧列表。
watch(() => route.query, () => {
  Object.assign(filters, readFilters())
  keyword.value = filters.q
  void load()
})
function selectFolder(folder: string) { filters.folder = folder; filters.page = 1 }
function toggleFolder(node: FolderNode) { if (collapsed.has(node.value)) collapsed.delete(node.value); else collapsed.add(node.value) }
function search() { filters.q = keyword.value.trim(); filters.page = 1 }
function clearFilters() { keyword.value = ''; Object.assign(filters, { q: '', category: '', stage: '', dates: null, page: 1 }) }

async function create(body: BidSettings) {
  if (creating.value) return
  creating.value = true; dialogError.value = ''
  try { const project = await bidApi.create(body); dialog.value = false; await router.push(`/bid/${project.id}`) }
  catch (e) { dialogError.value = e instanceof ApiError ? e.message : '新建项目失败，请重试' }
  finally { creating.value = false }
}
// 在某个文件夹里新建，默认放进这个文件夹
const defaultFolder = computed(() => filters.folder && filters.folder !== 'none' ? filters.folder : null)
function openDialog() { dialogError.value = ''; dialog.value = true }

// ---- 文件夹
const err = (e: unknown, fallback: string) => e instanceof ApiError ? e.message : fallback
async function newFolder(parent: FolderNode | null) {
  try {
    const { value } = await ElMessageBox.prompt(parent ? `在“${parent.label}”下新建` : '最多三级', '新建文件夹', {
      confirmButtonText: '新建', cancelButtonText: '取消', inputPlaceholder: '例如：2026 年',
      inputValidator: v => (!!v.trim() && v.trim().length <= 40) || '请输入 1～40 字' })
    await bidApi.createFolder(value.trim(), parent?.value ?? null)
    if (parent) collapsed.delete(parent.value)
    await load()
  } catch (e) { if (e instanceof ApiError) ElMessage.error(e.message) }
}
async function renameFolder(node: FolderNode) {
  try {
    const { value } = await ElMessageBox.prompt('最多 40 字', '文件夹改名', { inputValue: node.label, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: v => (!!v.trim() && v.trim().length <= 40) || '请输入 1～40 字' })
    await bidApi.renameFolder(node.value, value.trim())
    await load()
  } catch (e) { if (e instanceof ApiError) ElMessage.error(e.message) }
}
async function removeFolder(node: FolderNode) {
  const inner = node.children.length ? '其中的子文件夹一并删除，' : ''
  try { await ElMessageBox.confirm(`${inner}文件夹里的项目移到“未归档”，项目本身不删除。`, `删除文件夹“${node.label}”`, { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try {
    tree.value = await bidApi.removeFolder(node.value)
    if (filters.folder && !tree.value.folders.some(f => f.id === filters.folder) && filters.folder !== 'none') filters.folder = ''
    else void load()
  } catch (e) { ElMessage.error(err(e, '删除失败')) }
}

// ---- 拖动归档（键盘与读屏用行尾菜单“移到文件夹”）
const dragId = ref(''), dropTarget = ref<string | null>(null)
function dragStart(event: DragEvent, item: BidSummary) {
  dragId.value = item.id
  event.dataTransfer?.setData('text/plain', item.title)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
function dragEnd() { dragId.value = ''; dropTarget.value = null }
async function place(id: string, folder: string | null) {
  const item = items.value.find(i => i.id === id)
  if (!item || item.folder_id === folder) return
  try {
    await bidApi.place(id, folder)
    ElMessage.success(`已移到“${folder ? folderPath(tree.value.folders, folder) : '未归档'}”`)
    await load()
  } catch (e) { ElMessage.error(err(e, '移动失败')) }
}
function drop(folder: string | null) { const id = dragId.value; dragEnd(); if (id) void place(id, folder) }
const moving = ref<BidSummary>(), moveTo = ref<string | null>(null)
function openMove(item: BidSummary) { moving.value = item; moveTo.value = item.folder_id }
async function confirmMove() { if (moving.value) { const id = moving.value.id; moving.value = undefined; await place(id, moveTo.value) } }

onMounted(async () => {
  try { categories.value = (await bidApi.categories()).items } catch { /* 下拉为空 */ }
  await load()
  ready.value = true
})
// 全局搜索的“新建标书初审”带 ?new=1 来：直接打开新建对话框，并去掉地址里的参数
watch(() => route.query.new, value => { if (value) { openDialog(); void router.replace({ path: '/bid' }) } }, { immediate: true })
</script>

<template>
  <div class="page page--wide bid-home">
    <h1 class="entry-title">标书初审</h1>
    <p class="entry-lede">招标文件与各家投标文件在本机比对，找出不符合项与串标线索；不打分、不推荐中标人。</p>
    <div class="lead-actions">
      <el-button type="primary" @click="openDialog"><Plus :size="16" :stroke-width="1.75" />新建初审项目</el-button>
      <span v-if="auth.can('use.assistant')" class="aside">只是比较几份报价单？用<router-link to="/assistant">工作台的“比价”</router-link></span>
    </div>
    <el-alert v-if="error" class="alert" :title="error" type="error" :closable="false" />

    <section v-if="empty && !error" class="how" aria-label="一次初审的三步">
      <h2 class="si-overline">一次初审分三步</h2>
      <ol class="steps">
        <li v-for="step in STEPS" :key="step.no"><span class="no">{{ step.no }}</span><strong>{{ step.title }}</strong><p>{{ step.desc }}</p></li>
      </ol>
      <p class="hint">全部材料只在本机处理；选择“模型核对”时，也只发送你确认过的脱敏片段。</p>
    </section>

    <div v-else-if="ready" class="browser">
      <!-- 左：个人文件夹树（最多三级），可把项目拖到文件夹上 -->
      <nav class="folders" aria-label="文件夹">
        <button type="button" class="node" :class="{ selected: !filters.folder, drop: dropTarget === '' }" :aria-current="!filters.folder ? 'page' : undefined" @click="selectFolder('')">
          <Layers :size="16" :stroke-width="1.75" /><span class="label">全部项目</span><span class="count num">{{ tree.all }}</span>
        </button>
        <button type="button" class="node" :class="{ selected: filters.folder === 'none', drop: dropTarget === 'none' }" :aria-current="filters.folder === 'none' ? 'page' : undefined"
          @click="selectFolder('none')" @dragover.prevent="dropTarget = 'none'" @dragleave="dropTarget = null" @drop.prevent="drop(null)">
          <Inbox :size="16" :stroke-width="1.75" /><span class="label">未归档</span><span class="count num">{{ tree.unfiled }}</span>
        </button>
        <p class="group">我的文件夹</p>
        <div v-for="node in flat" :key="node.value" class="node-row" :class="{ selected: filters.folder === node.value, drop: dropTarget === node.value }" :style="{ '--depth': node.depth - 1 }"
          @dragover.prevent="dropTarget = node.value" @dragleave="dropTarget = null" @drop.prevent="drop(node.value)">
          <button v-if="node.children.length" type="button" class="folder-toggle" :aria-label="`${collapsed.has(node.value) ? '展开' : '收起'}文件夹“${node.label}”`" :aria-expanded="!collapsed.has(node.value)" @click="toggleFolder(node)">
            <ChevronRight v-if="collapsed.has(node.value)" :size="14" :stroke-width="1.75" /><ChevronDown v-else :size="14" :stroke-width="1.75" />
          </button>
          <span v-else class="folder-spacer" aria-hidden="true" />
          <button type="button" class="node" :aria-current="filters.folder === node.value ? 'page' : undefined" @click="selectFolder(node.value)">
            <Folder :size="16" :stroke-width="1.75" /><span class="label" :title="node.label">{{ node.label }}</span><span class="count num">{{ node.total }}</span>
          </button>
          <RowMore class="node-more">
            <el-dropdown-item v-if="node.depth < 3" @click="newFolder(node)">新建子文件夹</el-dropdown-item>
            <el-dropdown-item @click="renameFolder(node)">改名</el-dropdown-item>
            <el-dropdown-item divided class="danger-item" @click="removeFolder(node)">删除</el-dropdown-item>
          </RowMore>
        </div>
        <button type="button" class="node add" @click="newFolder(null)"><FolderPlus :size="16" :stroke-width="1.75" /><span class="label">新建文件夹</span></button>
        <p class="hint tip">把右侧项目拖到文件夹上即可归档。</p>
      </nav>

      <!-- 右：搜索、筛选与列表 -->
      <section class="list" aria-label="项目列表">
        <div class="filters">
          <el-input v-model="keyword" placeholder="搜索项目名称，回车查找" clearable aria-label="搜索项目名称" @keyup.enter="search" @clear="search">
            <template #prefix><Search :size="16" :stroke-width="1.75" /></template>
          </el-input>
          <el-select v-model="filters.category" placeholder="全部分类" clearable aria-label="分类" @change="filters.page = 1">
            <el-option v-for="c in categories" :key="c.id" :label="c.enabled ? c.name : `${c.name}（已停用）`" :value="String(c.id)" />
          </el-select>
          <el-select v-model="filters.stage" placeholder="全部阶段" clearable aria-label="阶段" @change="filters.page = 1">
            <el-option v-for="[value, label] in STAGES" :key="value" :label="label" :value="value" />
          </el-select>
          <div class="d"><el-date-picker v-model="filters.dates" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开标从" end-placeholder="开标到" aria-label="开标日期" @change="filters.page = 1" /></div>
        </div>
        <div class="list-head">
          <h2>{{ currentName }}</h2>
          <span class="hint num">{{ loading ? '正在读取…' : `共 ${total} 个` }}</span>
          <el-button v-if="narrowed" link type="primary" class="reset" @click="clearFilters">清除筛选</el-button>
        </div>
        <div :aria-busy="loading">
          <router-link v-for="item in items" :key="item.id" :to="`/bid/${item.id}`" class="list-row" :class="{ dragging: dragId === item.id }" draggable="true"
            @dragstart="dragStart($event, item)" @dragend="dragEnd">
            <span class="name-cell">
              <span class="name">{{ item.title }}</span>
              <span class="sub hint">
                <span v-if="item.category_id">{{ categoryName(item.category_id) }}</span>
                <span v-if="item.folder_id && item.folder_id !== filters.folder"><Folder :size="12" :stroke-width="1.75" />{{ folderPath(tree.folders, item.folder_id) }}</span>
              </span>
            </span>
            <span class="meta">
              <BidStatus class="state" v-bind="bidState(item)" />
              <span class="hint num opening">开标 {{ item.opening_date }}</span>
              <span class="hint num at">{{ formatDateTimeMinute(item.last_activity_at) }}</span>
              <span class="more" @click.prevent.stop><RowMore><el-dropdown-item @click="openMove(item)">移到文件夹…</el-dropdown-item></RowMore></span>
            </span>
          </router-link>
          <p v-if="!loading && !items.length" class="none hint">{{ narrowed ? '没有符合条件的项目。' : filters.folder ? '这个文件夹里还没有项目，可以把项目拖进来。' : '还没有项目。' }}</p>
        </div>
        <el-pagination v-if="total > 20" v-model:current-page="filters.page" class="pager" :total="total" :page-size="20" layout="prev, pager, next" />
      </section>
    </div>

    <BidSettingsDialog v-model="dialog" title="新建初审项目" confirm-text="创建项目" :default-folder="defaultFolder" :busy="creating" :error="dialogError" @submit="create" />
    <el-dialog :model-value="!!moving" title="移到文件夹" width="420px" append-to-body @update:model-value="moving = undefined">
      <p class="hint move-name">{{ moving?.title }}</p>
      <el-tree-select v-model="moveTo" :data="nodes" check-strictly default-expand-all :render-after-expand="false" clearable placeholder="未归档" class="move" />
      <template #footer><el-button @click="moving = undefined">取消</el-button><el-button type="primary" @click="confirmMove">移动</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bid-home .entry-lede { max-width: none; }
.lead-actions { display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.lead-actions .el-button svg { margin-right: 6px; }
.aside { font: var(--si-font-meta); color: var(--si-ink-3); }
.aside a { margin-left: 2px; }
.alert { margin-top: 20px; }

.browser { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 32px; margin-top: 32px; }
.folders { display: flex; flex-direction: column; gap: 2px; padding-right: 16px; border-right: 1px solid var(--si-hair); align-self: start; }
.group { margin: 14px 0 4px; padding: 0 10px; font: var(--si-font-overline); color: var(--si-ink-3); }
.node-row { position: relative; display: flex; align-items: center; padding-left: calc(var(--depth) * 16px); border-radius: var(--el-border-radius-base); }
.folder-toggle, .folder-spacer { flex: none; width: 24px; height: 34px; }
.folder-toggle { display: inline-flex; align-items: center; justify-content: center; padding: 0; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--si-ink-3); cursor: pointer; }
.folder-toggle:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.folders > .node { flex: none; }
.node { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 10px; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--si-ink-2); font: var(--si-font-label); text-align: left; cursor: pointer; }
.node svg { flex: none; color: var(--si-ink-3); }
.node .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node .count { flex: none; font: var(--si-font-meta); color: var(--si-ink-4); }
.node:hover, .node-row:hover .node { background: var(--si-hover-bg); color: var(--si-ink-1); }
.node.selected, .node-row.selected .node { background: var(--si-select-bg); color: var(--si-ink-1); font-weight: 600; }
.node.drop, .node-row.drop .node { outline: 1px solid var(--si-brand); background: var(--si-blue-tint); }
.node-more { position: absolute; right: 4px; opacity: 0; }
.node-row .node { padding-right: 30px; }
.node-row:hover .node-more, .node-row:focus-within .node-more { opacity: 1; }
.add { margin-top: 6px; color: var(--si-ink-3); }
.tip { margin: 10px 10px 0; }

.filters { display: grid; grid-template-columns: minmax(200px, 1fr) 128px 128px 256px; gap: 12px; }
.d :deep(.el-date-editor) { width: 100%; box-sizing: border-box; }
.list-head { display: flex; align-items: baseline; gap: 12px; margin: 24px 0 8px; padding-bottom: 10px; border-bottom: 1px solid var(--si-hair-strong); }
.list-head h2 { margin: 0; font: var(--si-font-h3); }
.reset { margin-left: auto; }
.list-row { cursor: grab; }
.list-row.dragging { opacity: .5; }
.name-cell { flex: 1; min-width: 0; display: flex; flex-direction: column; padding: 8px 0; }
.sub { display: flex; gap: 12px; font: var(--si-font-meta); }
.sub:empty { display: none; }
.sub span { display: inline-flex; align-items: center; gap: 4px; }
.meta { flex: none; display: flex; align-items: center; gap: 20px; }
.state { width: 96px; }
.opening { width: 112px; }
.at { width: 116px; text-align: right; }
.more { width: 24px; display: inline-flex; justify-content: center; }
.none { padding: 24px 12px; margin: 0; }
.pager { margin-top: 16px; }
.move { width: 100%; }
.move-name { margin: 0 0 12px; }

.how { margin-top: 48px; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); margin: 0 0 16px; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); border-bottom: 1px solid var(--si-hair); }
.steps li { padding: 20px 20px 22px; border-left: 1px solid var(--si-hair-faint); }
.steps li:first-child { border-left: 0; padding-left: 0; }
.no { display: block; margin-bottom: 14px; font: var(--si-font-code-sm); color: var(--si-ink-4); }
.steps strong { font: var(--si-font-h3); color: var(--si-ink-1); }
.steps p { margin: 6px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
</style>
