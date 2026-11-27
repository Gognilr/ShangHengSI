<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 标书初审 · 项目列表（澄明 8.5 ①，列表页模板 1200）：页头一句说明 + 新建；左文件夹树卡片 240；右分段筛选、搜索、分类、开标日期，项目一行一张卡。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChevronDown, ChevronRight, FileStack, Folder, FolderOpen, FolderPlus, Inbox, ListChecks, Plus, ScanSearch, Search, UploadCloud } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, type BidCategory, type BidFolderTree, type BidListQuery, type BidOpeningRuler, type BidSettings, type BidSummary } from '@/api/bid'
import { formatDateTimeMinute, formatShort } from '@/utils/time'
import { bidTrip } from '@/utils/trip'
import { useAuthStore } from '@/stores/auth'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import BidSettingsDialog from '@/components/bid/BidSettingsDialog.vue'
import OpeningRuler from '@/components/bid/OpeningRuler.vue'
import RowMore from '@/components/RowMore.vue'
import { folderOptions, folderPath, type FolderNode } from '@/components/bid/folders'

const router = useRouter(), route = useRoute(), auth = useAuthStore()
const items = ref<BidSummary[]>([]), total = ref(0), loading = ref(true), error = ref(''), ready = ref(false)
const tree = ref<BidFolderTree>({ folders: [], all: 0, unfiled: 0 }), categories = ref<BidCategory[]>([])
const ruler = ref<BidOpeningRuler>()
const creating = ref(false), dialog = ref(false), dialogError = ref('')
const STEPS = [
  { icon: UploadCloud, title: '招标文件与条款', desc: '开标前上传招标文件，核对 ★、否决与“必须 / 不得”条款。' },
  { icon: FolderOpen, title: '投标文件', desc: '把各家的文件夹一起拖进来，每个文件夹建一家；带密码的 PDF 就地输入。' },
  { icon: ScanSearch, title: '检查与问题清单', desc: '开标时本机找出否决类问题与串标线索，定位到原件页，导出问题清单。' },
]
type Phase = '' | 'active' | 'done'
const PHASES: { value: Phase; label: string }[] = [{ value: '', label: '全部' }, { value: 'active', label: '进行中' }, { value: 'done', label: '已完成' }]

// 筛选条件放在地址里，从项目返回时还原（V34-B-01a）
const str = (v: unknown) => typeof v === 'string' ? v : ''
const readFilters = () => ({
  q: str(route.query.q), folder: str(route.query.folder), category: str(route.query.category),
  stage: (['active', 'done'].includes(str(route.query.stage)) ? str(route.query.stage) : '') as Phase,
  dates: (str(route.query.from) && str(route.query.to) ? [str(route.query.from), str(route.query.to)] : null) as [string, string] | null,
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
// 开标日距今天（上海时区，由时间尺接口给出）：今天、n 天后；已过的不写
function fromToday(date: string) {
  const today = ruler.value?.today
  if (!today) return ''
  const d = Math.round((Date.parse(date) - Date.parse(today)) / 86400000)
  return d === 0 ? ' · 今天' : d > 0 ? ` · ${d} 天后` : ''
}
function sub(item: BidSummary) {
  const parts = [categoryName(item.category_id), `开标 ${item.opening_date.slice(5)}${fromToday(item.opening_date)}`, item.bidder_count ? `${item.bidder_count} 家投标人` : '还没有投标人']
  if (item.folder_id && item.folder_id !== filters.folder) parts.push(folderPath(tree.value.folders, item.folder_id))
  return parts.filter(Boolean).join(' · ')
}
const projects = computed(() => items.value.map(item => {
  const trip = bidTrip({ ...item, checkFailed: item.check_failed, ocrState: item.ocr_state, modelState: item.model_state })
  const preliminary = ['scanning', 'updating', 'failed', 'expired'].includes(item.ocr_state ?? '')
  const conclusion = item.stage !== 'checked' || item.severe === undefined ? '' : item.severe
    ? `${preliminary ? '初步 · ' : ''}严重 ${item.severe}`
    : preliminary ? '初步 · 未列出严重问题' : '没有严重问题'
  return { item, trip, conclusion }
}))

let generation = 0, timer: ReturnType<typeof setTimeout> | undefined
async function load(quiet = false) {
  const current = ++generation
  clearTimeout(timer)
  if (!quiet) loading.value = true
  error.value = ''
  try {
    const { q, category, stage, from, to } = query.value
    const [data, folders] = await Promise.all([bidApi.list({ ...query.value, opening_view: true }), bidApi.folders({ q, category, stage, from, to })])
    if (current !== generation) return
    items.value = data.items; total.value = data.total; tree.value = folders; ruler.value = data.opening_ruler
    if (projects.value.some(({ trip }) => trip.state === 'run')) timer = setTimeout(() => void load(true), 3000)
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
function setPhase(v: Phase) { filters.stage = v; filters.page = 1 }
function setOpeningDate(date: string | null) { filters.dates = date ? [date, date] : null; filters.page = 1 }

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

// ---- 拖动归档（键盘与读屏用卡片菜单“移到文件夹”）
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
onBeforeUnmount(() => { generation++; clearTimeout(timer) })
// 全局搜索的“新建标书初审”带 ?new=1 来：直接打开新建对话框，并去掉地址里的参数
watch(() => route.query.new, value => { if (value) { openDialog(); void router.replace({ path: '/bid' }) } }, { immediate: true })
</script>

<template>
  <div class="page bid-home">
    <header class="head">
      <div class="head-text">
        <h1 class="entry-title">标书初审</h1>
        <p class="entry-lede">招标文件与各家投标文件在本机比对，开标时找出否决类问题与串标线索。不打分，不推荐中标人。</p>
      </div>
      <el-button type="primary" size="large" class="create" @click="openDialog"><Plus :size="18" :stroke-width="1.75" />新建初审项目</el-button>
    </header>
    <p v-if="auth.can('use.assistant')" class="aside">只是比较几份报价单？用<router-link to="/assistant">工作台的“比价”</router-link></p>
    <SiNotice v-if="error" class="alert" :title="error" type="error" :closable="false" />
    <p v-if="!ready && loading" class="hint" role="status">正在读取项目…</p>

    <!-- 还没有任何项目：三步说明卡片 -->
    <section v-if="empty && !error" class="how si-stagger" aria-label="一次初审分三步">
      <article v-for="(step, i) in STEPS" :key="step.title" class="si-card how-card">
        <span class="how-ic"><component :is="step.icon" :size="20" :stroke-width="1.75" /></span>
        <span class="how-no num">0{{ i + 1 }}</span>
        <b>{{ step.title }}</b>
        <p>{{ step.desc }}</p>
      </article>
      <p class="how-foot hint">全部材料只在本机处理；选择模型补查时，也只发送你确认过的、遮住名称后的片段。</p>
    </section>

    <div v-else-if="ready" class="browser">
      <!-- 左：个人文件夹树（最多三级），可把项目拖到文件夹上 -->
      <nav class="tree si-card" aria-label="文件夹">
        <button type="button" class="node" :class="{ selected: !filters.folder, drop: dropTarget === '' }" :aria-current="!filters.folder ? 'page' : undefined" @click="selectFolder('')">
          <component :is="!filters.folder ? FolderOpen : Folder" :size="16" :stroke-width="1.75" /><span class="label">全部项目</span><span class="count num">{{ tree.all }}</span>
        </button>
        <button type="button" class="node" :class="{ selected: filters.folder === 'none', drop: dropTarget === 'none' }" :aria-current="filters.folder === 'none' ? 'page' : undefined"
          @click="selectFolder('none')" @dragover.prevent="dropTarget = 'none'" @dragleave="dropTarget = null" @drop.prevent="drop(null)">
          <Inbox :size="16" :stroke-width="1.75" /><span class="label">未归档</span><span class="count num">{{ tree.unfiled }}</span>
        </button>
        <p v-if="flat.length" class="group">我的文件夹</p>
        <div v-for="node in flat" :key="node.value" class="node-row" :class="{ selected: filters.folder === node.value, drop: dropTarget === node.value }" :style="{ '--depth': node.depth - 1 }"
          @dragover.prevent="dropTarget = node.value" @dragleave="dropTarget = null" @drop.prevent="drop(node.value)">
          <button v-if="node.children.length" type="button" class="folder-toggle" :aria-label="`${collapsed.has(node.value) ? '展开' : '收起'}文件夹“${node.label}”`" :aria-expanded="!collapsed.has(node.value)" @click="toggleFolder(node)">
            <ChevronRight v-if="collapsed.has(node.value)" :size="14" :stroke-width="1.75" /><ChevronDown v-else :size="14" :stroke-width="1.75" />
          </button>
          <span v-else class="folder-spacer" aria-hidden="true" />
          <button type="button" class="node" :aria-current="filters.folder === node.value ? 'page' : undefined" @click="selectFolder(node.value)">
            <component :is="filters.folder === node.value ? FolderOpen : Folder" :size="16" :stroke-width="1.75" /><span class="label" :title="node.label">{{ node.label }}</span><span class="count num">{{ node.total }}</span>
          </button>
          <RowMore class="node-more">
            <el-dropdown-item v-if="node.depth < 3" @click="newFolder(node)">新建子文件夹</el-dropdown-item>
            <el-dropdown-item @click="renameFolder(node)">改名</el-dropdown-item>
            <el-dropdown-item divided class="danger-item" @click="removeFolder(node)">删除</el-dropdown-item>
          </RowMore>
        </div>
        <button type="button" class="node add" @click="newFolder(null)"><FolderPlus :size="16" :stroke-width="1.75" /><span class="label">新建文件夹</span></button>
        <p class="tip">把右侧项目拖到文件夹上即可归档。</p>
      </nav>

      <!-- 右：分段筛选、搜索、分类、开标日期与项目卡片 -->
      <section class="list" aria-label="项目列表">
        <OpeningRuler v-if="ruler" :ruler="ruler" :dates="filters.dates" @filter="setOpeningDate" />
        <div class="bar">
          <SiSegmented :model-value="filters.stage" :options="PHASES" ariaLabel="进度" @update:model-value="v => setPhase(v as Phase)" />
          <el-input v-model="keyword" class="search" placeholder="搜索项目名，回车查找" clearable aria-label="搜索项目名称" @keyup.enter="search" @clear="search">
            <template #prefix><Search :size="16" :stroke-width="1.75" /></template>
          </el-input>
          <el-select v-model="filters.category" class="cat" placeholder="全部分类" clearable aria-label="分类" @change="filters.page = 1">
            <el-option v-for="c in categories" :key="c.id" :label="c.enabled ? c.name : `${c.name}（已停用）`" :value="String(c.id)" />
          </el-select>
          <div class="dates"><el-date-picker v-model="filters.dates" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开标从" end-placeholder="开标到" aria-label="开标日期" @change="filters.page = 1" /></div>
        </div>
        <div class="list-head">
          <h2>{{ currentName }}</h2>
          <span class="hint num">{{ loading ? '正在读取…' : `${total} 个项目` }}</span>
          <el-button v-if="narrowed" link type="primary" class="reset" @click="clearFilters">清除筛选</el-button>
        </div>
        <div class="cards si-stagger" :aria-busy="loading">
          <router-link v-for="{ item, trip, conclusion } in projects" :key="item.id" :to="`/bid/${item.id}`" class="proj si-card si-lift" :class="{ dragging: dragId === item.id }" draggable="true"
            @dragstart="dragStart($event, item)" @dragend="dragEnd">
            <span class="si-mod-icon si-lift-icon mod" aria-hidden="true"><FileStack :size="20" :stroke-width="1.75" /></span>
            <span class="main"><b :title="item.title">{{ item.title }}</b><span>{{ sub(item) }}</span></span>
            <span class="row-state">
              <SiTrip :trip="trip" :hide-text="trip.state === 'done' && !!conclusion" />
              <span v-if="conclusion" class="si-badge" :class="{ 'is-hi': item.severe }"><i v-if="item.severe" class="d" />{{ conclusion }}</span>
            </span>
            <span class="at num" :title="formatDateTimeMinute(item.last_activity_at)">{{ formatShort(item.last_activity_at) }}</span>
            <span class="more" @click.prevent.stop><RowMore><el-dropdown-item @click="openMove(item)">移到文件夹…</el-dropdown-item></RowMore></span>
            <ChevronRight class="chev" :size="16" :stroke-width="1.75" aria-hidden="true" />
          </router-link>
        </div>
        <div v-if="!loading && !error && !items.length" class="none si-card">
          <span class="none-ic"><ListChecks :size="22" :stroke-width="1.75" /></span>
          <b>{{ narrowed ? '没有符合条件的项目' : filters.folder ? '这个文件夹里还没有项目' : '还没有项目' }}</b>
          <p class="hint">{{ narrowed ? '换个条件，或清除筛选。' : filters.folder ? '可以把项目拖进来，或在这里新建。' : '新建一个初审项目，先上传招标文件。' }}</p>
          <el-button v-if="narrowed" @click="clearFilters">清除筛选</el-button>
          <el-button v-else type="primary" @click="openDialog"><Plus :size="16" :stroke-width="1.75" />新建初审项目</el-button>
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
/* 列表页模板 ④：内容宽 1200 */
.bid-home { max-width: 1200px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.head-text { min-width: 0; }
.entry-lede { margin-bottom: 0; max-width: 720px; }
.create { flex: none; margin-top: 4px; }
.create :deep(> span) { gap: 6px; }
.aside { margin: 10px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.aside a { margin-left: 2px; color: var(--si-blue-ink); }
.alert { margin-top: 20px; }

.how { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 32px; }
.how-card { position: relative; display: flex; flex-direction: column; gap: 6px; padding: 22px 22px 24px; }
.how-ic { display: grid; place-items: center; width: 44px; height: 44px; margin-bottom: 8px; border-radius: var(--si-radius-md); background: var(--si-m-bid-soft); color: var(--si-m-bid); }
.how-no { position: absolute; top: 20px; right: 22px; font: var(--si-font-code-sm); color: var(--si-ink-5); }
.how-card b { font: var(--si-font-h3); color: var(--si-ink-1); }
.how-card p { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.how-foot { grid-column: 1 / -1; margin: 4px 0 0; }

.browser { display: flex; align-items: flex-start; gap: 24px; margin-top: 32px; }
.tree { flex: none; width: 240px; box-sizing: border-box; padding: 8px; display: flex; flex-direction: column; gap: 2px; }
.group { margin: 12px 0 4px; padding: 0 10px; font: var(--si-font-overline); color: var(--si-ink-4); }
.node-row { position: relative; display: flex; align-items: center; padding-left: calc(var(--depth) * 16px); border-radius: var(--si-radius-control); }
.folder-toggle, .folder-spacer { flex: none; width: 22px; height: 36px; }
.folder-toggle { display: inline-flex; align-items: center; justify-content: center; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-4); cursor: pointer; }
.folder-toggle:hover { color: var(--si-ink-1); }
.tree > .node { flex: none; }
.node { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 10px; border: 0; border-radius: var(--si-radius-control); background: transparent; color: var(--si-ink-2); font: var(--si-font-label); text-align: left; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out), color var(--si-d2) var(--si-ease-out); }
.node svg { flex: none; color: var(--si-ink-3); }
.node .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node .count { flex: none; font: var(--si-font-caption); color: var(--si-ink-4); }
.node:hover, .node-row:hover .node { background: var(--si-hover-bg); color: var(--si-ink-1); }
.node.selected, .node-row.selected .node { background: var(--si-select-bg); color: var(--si-ink-1); font-weight: 600; }
.node.selected svg, .node-row.selected .node svg { color: var(--si-m-bid); }
.node.drop, .node-row.drop .node { box-shadow: inset 0 0 0 1.5px var(--si-brand-2); background: var(--si-blue-tint); }
.node-more { position: absolute; right: 4px; opacity: 0; }
.node-row .node { padding-right: 30px; }
.node-row:hover .node-more, .node-row:focus-within .node-more { opacity: 1; }
.add { margin-top: 4px; color: var(--si-ink-3); }
.tip { margin: 8px 10px 4px; font: var(--si-font-caption); color: var(--si-ink-4); }

.list { flex: 1; min-width: 0; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 16px; }
.search { flex: 1; min-width: 150px; max-width: 260px; }
.cat { flex: none; width: 132px; }
.dates { flex: none; width: 236px; }
.dates :deep(.el-date-editor) { width: 100%; box-sizing: border-box; }
.list-head { display: flex; align-items: baseline; gap: 12px; margin: 22px 0 12px; }
.list-head h2 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.reset { margin-left: auto; }
.cards { display: flex; flex-direction: column; gap: 10px; }
.proj { display: flex; align-items: center; gap: 16px; padding: 14px 16px 14px 20px; color: inherit; text-decoration: none; cursor: pointer; }
.proj:focus-visible { outline: none; box-shadow: var(--si-ring), var(--si-shadow-2); }
.proj.dragging { opacity: .5; }
.mod { --mc: var(--si-m-bid); --mc-soft: var(--si-m-bid-soft); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.main b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h3); color: var(--si-ink-1); }
.main span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-meta); color: var(--si-ink-3); }
.row-state { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 4px 10px; max-width: 44%; }
.at { flex: none; text-align: right; font: var(--si-font-meta); color: var(--si-ink-3); }
.more { flex: none; width: 28px; display: inline-flex; justify-content: center; }
.chev { flex: none; color: var(--si-ink-4); }
.none { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 40px 24px; text-align: center; }
.none-ic { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 6px; border-radius: 50%; background: var(--si-m-bid-soft); color: var(--si-m-bid); }
.none b { font: var(--si-font-h3); color: var(--si-ink-1); }
.none p { margin: 0 0 10px; }
.pager { margin-top: 16px; }
.move { width: 100%; }
.move-name { margin: 0 0 12px; }
</style>
