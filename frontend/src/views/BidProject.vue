<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Check, FileDown, FolderOpen, ListChecks, Plus, ScanSearch, Settings2, UploadCloud } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidPolicyLabel, bidState, type BidProject, type BidSettings } from '@/api/bid'
import RowMore from '@/components/RowMore.vue'
import EmptyState from '@/components/EmptyState.vue'
import BidFiles from '@/components/bid/BidFiles.vue'
import BidStatus from '@/components/bid/BidStatus.vue'
import BidderCard from '@/components/bid/BidderCard.vue'
import BidSettingsDialog from '@/components/bid/BidSettingsDialog.vue'
import BidRequirements from '@/components/bid/BidRequirements.vue'
import BidClues from '@/components/bid/BidClues.vue'

const route = useRoute(), router = useRouter(), id = computed(() => String(route.params.id))
const project = ref<BidProject>(), loading = ref(false), error = ref(''), pending = ref(false), percent = ref<number | null>(null)
const step = ref(0)
const locked = computed(() => !!project.value && !['preparing', 'ready'].includes(project.value.stage))  // 开始初审后材料锁定
const blocked = computed(() => pending.value || locked.value)  // 解析期间仍可继续加文件、改名称，新文件由同一次解析接着处理
const tenderFiles = computed(() => project.value?.files.filter(f => !f.bidder_id) ?? [])
const bidFiles = computed(() => project.value?.files.filter(f => f.bidder_id) ?? [])
const filesOf = (bidder: string) => project.value?.files.filter(f => f.bidder_id === bidder) ?? []
const rowErrors = reactive<Record<string, string>>({})  // 密码错误等提示显示在对应文件行，不只在页顶
const adding = ref(false), newName = ref('')
const picker = ref<HTMLInputElement>(), folders = ref<HTMLInputElement>(), target = ref<string>()
const dragging = ref<'tender' | 'bulk' | null>(null), groupDialog = ref(false), settingsDialog = ref(false), settingsError = ref('')
const grouped = ref<{ original: string; name: string; files: File[] }[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0, edits = 0  // edits：轮询结果晚于修改返回时丢弃，避免旧状态覆盖新状态

// 四步条：前两步按材料是否齐全打勾；后两步在要求清单与初审完成后开放
const steps = computed(() => {
  const p = project.value
  const tenderDone = !!tenderFiles.value.length && tenderFiles.value.every(f => f.state === 'done') && p?.stage !== 'preparing'
  const bidDone = !!p && p.bidders.length > 0 && p.bidders.every(b => filesOf(b.id).length > 0) && bidFiles.value.every(f => f.state === 'done')
  return [
    { title: '招标文件与要求', done: tenderDone },
    { title: '投标文件', done: bidDone },
    { title: '初审结果', done: p?.stage === 'checked' },
    { title: '导出', done: false },
  ]
})
// 已解析的招标文件变化时，清单组件重新读取
const tenderKey = computed(() => tenderFiles.value.filter(f => f.state === 'done').map(f => f.id).join(','))
const parseNote = computed(() => {
  const files = project.value?.files ?? []
  if (!files.length) return null
  const done = files.filter(f => f.state === 'done').length, failed = files.filter(f => f.state === 'failed').length
  if (project.value?.busy) return { tone: 'run' as const, label: `正在解析 ${done} / ${files.length}` }
  if (failed) return { tone: 'warn' as const, label: `${failed} 份文件待处理` }
  return { tone: 'ok' as const, label: `${files.length} 份文件已解析` }
})

function resetTimer() { if (timer) clearTimeout(timer); timer = undefined }
function accept(data: BidProject) {
  project.value = data
  resetTimer()
  if (data.busy || data.stage === 'checking') timer = setTimeout(() => void load(false), 1500)
}
async function load(initial: boolean) {
  const current = generation, edit = edits
  if (initial) loading.value = true
  try {
    const data = await bidApi.get(id.value)
    if (current !== generation || edit !== edits) return
    accept(data)
    if (initial && data.bidders.length && !tenderFiles.value.length) step.value = 1
  } catch (e) { if (current === generation) error.value = e instanceof ApiError ? e.message : '项目读取失败' }
  finally { if (current === generation) loading.value = false }
}
watch(id, () => { generation++; resetTimer(); project.value = undefined; error.value = ''; step.value = 0; grouped.value = []; groupDialog.value = false; void load(true) }, { immediate: true })
onBeforeUnmount(() => { generation++; resetTimer() })
async function mutate(action: () => Promise<BidProject>) {
  if (blocked.value) return
  pending.value = true; error.value = ''; resetTimer(); edits++
  const current = generation
  try { const data = await action(); if (current === generation) accept(data) }
  catch (e) { if (current === generation) error.value = e instanceof ApiError ? e.message : '操作失败，请重试' }
  finally { pending.value = false }
}
function openSettings() { settingsError.value = ''; settingsDialog.value = true }
async function saveSettings(body: BidSettings) {
  await mutate(() => bidApi.update(id.value, body))
  if (error.value) { settingsError.value = error.value; error.value = '' } else settingsDialog.value = false
}
async function removeFile(fid: string) { delete rowErrors[fid]; await mutate(() => bidApi.removeFile(id.value, fid)) }
async function setPassword(fid: string, password: string) {
  delete rowErrors[fid]
  await mutate(() => bidApi.password(id.value, fid, password))
  if (error.value) { rowErrors[fid] = error.value; error.value = '' }
}
// 开始初审：要求清单确认、文件解析完、至少一家有投标文件
const startBlock = computed(() => {
  const p = project.value
  if (!p) return '…'
  if (p.stage !== 'ready') return locked.value ? '' : '先在第 1 步确认关键参数与要求清单'
  if (p.busy || p.files.some(f => f.state === 'waiting')) return '文件还在解析'
  if (!bidFiles.value.some(f => f.state === 'done')) return '请先加入投标文件'
  return ''
})
async function startCheck() { await mutate(() => bidApi.startCheck(id.value)); if (!error.value) step.value = 2 }
const checkKey = computed(() => `${project.value?.stage}:${project.value?.check?.state}`)
async function reparse() { await mutate(() => bidApi.parse(id.value)) }
async function renameBidder(bidder: string, name: string) { await mutate(() => bidApi.renameBidder(id.value, bidder, name)) }
async function removeBidder(bidder: string) { await mutate(() => bidApi.removeBidder(id.value, bidder)) }
async function addBidder() {
  if (!newName.value.trim()) return
  await mutate(() => bidApi.addBidder(id.value, newName.value.trim()))
  if (!error.value) { newName.value = ''; adding.value = false }
}
function choose(bidder?: string) { if (!blocked.value) { target.value = bidder; picker.value?.click() } }
async function upload(files: File[], bidder?: string, groups?: string[]) {
  if (!files.length || !project.value) return
  if (files.some(f => f.size > project.value!.limits.file_mb * 1024 * 1024)) { error.value = `单个文件不能超过 ${project.value.limits.file_mb} MB`; return }
  percent.value = 0
  await mutate(() => bidApi.upload(id.value, files, bidder, groups, (loaded, total) => { percent.value = Math.round(loaded / total * 100) }))
  percent.value = null
}
function picked(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; void upload(files, target.value)
}

// 文件夹逐批读取直到空批次，避免浏览器只返回前 100 个条目。根目录只包一层时展开到各投标人子目录。
interface Entry { isFile: boolean; isDirectory: boolean; name: string; file: (ok: (file: File) => void, fail: (e: unknown) => void) => void; createReader: () => { readEntries: (ok: (entries: Entry[]) => void, fail: (e: unknown) => void) => void } }
async function walk(entry: Entry, path: string): Promise<{ path: string; file: File }[]> {
  if (entry.isFile) return [{ path: path + entry.name, file: await new Promise<File>((ok, fail) => entry.file(ok, fail)) }]
  const reader = entry.createReader(), children: Entry[] = []
  while (true) { const batch = await new Promise<Entry[]>((ok, fail) => reader.readEntries(ok, fail)); if (!batch.length) break; children.push(...batch) }
  const output: { path: string; file: File }[] = []
  for (const child of children) output.push(...await walk(child, `${path}${entry.name}/`))
  return output
}
function prepareGroups(list: { path: string; file: File }[]) {
  const usable = list.filter(i => !i.path.split('/').some(p => p.startsWith('.') || p === '__MACOSX') && !i.file.name.startsWith('~$'))
  if (!usable.length) { error.value = '文件夹中没有可上传的文件'; return }
  const paths = usable.map(i => i.path.replace(/\\/g, '/').split('/'))
  const roots = new Set(paths.map(p => p[0]))
  const unwrap = roots.size === 1 && paths.every(p => p.length >= 3)
  const map = new Map<string, File[]>()
  usable.forEach((item, index) => {
    const parts = paths[index]!, name = parts.length > 1 ? parts[unwrap ? 1 : 0]! : ''
    if (!map.has(name)) map.set(name, [])
    map.get(name)!.push(item.file)
  })
  grouped.value = Array.from(map, ([name, files]) => ({ original: name || '（未放在文件夹中的文件）', name, files })); groupDialog.value = true
}
function pickedFolders(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; prepareGroups(files.map(file => ({ file, path: file.webkitRelativePath || file.name })))
}
async function drop(event: DragEvent, bidder?: string, folderMode = false) {
  dragging.value = null
  if (blocked.value) return
  // 必须在首次 await 前取得全部入口，部分浏览器在事件结束后清除 dataTransfer。
  const entries = Array.from(event.dataTransfer?.items ?? []).map(item => item.webkitGetAsEntry()).filter(Boolean) as unknown as Entry[]
  const flat = Array.from(event.dataTransfer?.files ?? [])
  try {
    const all: { path: string; file: File }[] = []
    for (const entry of entries) all.push(...await walk(entry, ''))
    const files = all.length ? all : flat.map(file => ({ file, path: file.name }))
    if (folderMode) prepareGroups(files)
    else await upload(files.map(i => i.file), bidder)
  } catch { error.value = '文件夹读取失败，请用“导入文件夹”或逐家加入文件' }
}
const groupError = ref('')
async function confirmGroups() {
  const names = grouped.value.map(g => g.name.trim())
  if (names.some(n => !n) || new Set(names).size !== names.length) { groupError.value = '请为每组填写不同的投标人名称'; return }
  groupError.value = ''
  const files: File[] = [], groups: string[] = []
  grouped.value.forEach((g, i) => g.files.forEach(f => { files.push(f); groups.push(names[i]!) }))
  await upload(files, undefined, groups)
  if (error.value) { groupError.value = error.value; error.value = '' }
  else { groupDialog.value = false; grouped.value = []; step.value = 1 }
}
async function removeProject() {
  if (!project.value || blocked.value) return
  try { await ElMessageBox.confirm('将删除本项目的全部原件、密码与记录，且无法恢复。', '删除初审项目', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  pending.value = true
  try { await bidApi.remove(id.value); await router.push('/bid') }
  catch (e) { error.value = e instanceof ApiError ? e.message : '删除失败' }
  finally { pending.value = false }
}
</script>

<template>
  <div class="page page--wide bid-project">
    <nav class="breadcrumb" aria-label="位置"><router-link to="/bid">标书初审</router-link><span aria-hidden="true">/</span><span>{{ project?.title ?? '项目' }}</span></nav>
    <p v-if="loading" role="status" class="hint">正在读取项目…</p>
    <el-alert v-if="error && !project" :title="error" type="error" :closable="false" />
    <template v-if="project">
      <header class="head">
        <div class="title">
          <h1>{{ project.title }}</h1>
          <p class="meta"><span class="num">开标 {{ project.opening_date }}</span><i aria-hidden="true">·</i><BidStatus v-bind="bidState(project)" /><i aria-hidden="true">·</i><span>{{ bidPolicyLabel[project.policy] }}</span><i aria-hidden="true">·</i><span>仅本人可见</span></p>
        </div>
        <div class="head-actions">
          <el-button :disabled="blocked" @click="openSettings"><Settings2 :size="16" :stroke-width="1.75" />项目设置</el-button>
          <RowMore :disabled="blocked">
            <el-dropdown-item :disabled="!project.files.length" @click="reparse">重新解析全部文件</el-dropdown-item>
            <el-dropdown-item divided class="danger-item" @click="removeProject">删除项目</el-dropdown-item>
          </RowMore>
        </div>
      </header>

      <nav class="steps" aria-label="标书初审步骤">
        <button v-for="(s, index) in steps" :key="s.title" type="button" :class="{ current: step === index, done: s.done && step !== index }" :aria-current="step === index ? 'step' : undefined" @click="step = index">
          <span class="number"><Check v-if="s.done && step !== index" :size="14" :stroke-width="2" /><template v-else>{{ index + 1 }}</template></span>{{ s.title }}
        </button>
        <span class="progress" role="status">
          <span v-if="percent !== null" class="hint num">正在上传 {{ percent }}%</span>
          <BidStatus v-else-if="parseNote" v-bind="parseNote" />
        </span>
      </nav>
      <el-alert v-if="error" class="alert" :title="error" type="error" show-icon @close="error = ''" />

      <!-- 第 1 步：招标文件与要求 -->
      <div v-if="step === 0" :class="tenderKey ? 'tender-ready' : 'tender'">
        <section class="col">
          <div class="section-head"><h2 class="si-overline">招标文件</h2><span class="hint num">{{ tenderFiles.length }} / {{ project.limits.tender_files }}</span></div>
          <BidFiles v-if="tenderFiles.length" :project-id="id" :files="tenderFiles" :disabled="blocked" :busy="project.busy" :row-errors="rowErrors" @remove="removeFile" @password="setPassword" />
          <div v-if="tenderFiles.length < project.limits.tender_files" class="si-drop" :class="{ dragging: dragging === 'tender', slim: tenderFiles.length > 0 }" :aria-disabled="blocked" role="button" tabindex="0"
               @click="choose()" @keydown.enter="choose()" @dragover.prevent="dragging = 'tender'" @dragleave.self="dragging = null" @drop.prevent="drop($event)">
            <UploadCloud :size="tenderFiles.length ? 18 : 24" :stroke-width="1.75" />
            <p class="si-drop-title">{{ tenderFiles.length ? '加入答疑、补遗或澄清文件' : '拖入招标文件，或点击选择' }}</p>
            <p v-if="!tenderFiles.length" class="hint">Word 或 PDF；正文、答疑、补遗与澄清可分别加入，最多 {{ project.limits.tender_files }} 份</p>
          </div>
        </section>
        <BidRequirements v-if="tenderKey" class="requirements" :project-id="id" :policy="project.policy" :disabled="blocked" :tender-key="tenderKey" @changed="load(false)" />
        <aside v-else class="col next">
          <div class="section-head"><h2 class="si-overline">关键参数与要求清单</h2></div>
          <p class="next-lede">招标文件解析后，系统自动抓出 ★ 条款、否决条款与关键参数（最高限价、有效期、保证金等），由你核对确认。</p>
          <ul class="groups">
            <li v-for="g in ['资格条件', '实质性条款（★）', '否决条款', '报价要求', '有效期与保证金', '签章与格式页']" :key="g"><ListChecks :size="14" :stroke-width="1.75" aria-hidden="true" />{{ g }}<span class="num">—</span></li>
          </ul>
          <p class="hint">{{ project.busy ? '正在解析招标文件…' : '加入招标文件后开始。' }}</p>
        </aside>
        <footer class="step-foot"><el-button @click="step = 1">下一步：投标文件</el-button></footer>
      </div>

      <!-- 第 2 步：投标文件 -->
      <div v-else-if="step === 1" class="bids">
        <div class="toolbar">
          <div class="counts"><h2 class="si-overline">投标人 {{ project.bidders.length }} 家</h2><span class="hint num">投标文件 {{ bidFiles.length }} / {{ project.limits.bid_files }}</span></div>
          <div class="tools">
            <el-button :disabled="blocked" @click="adding = true"><Plus :size="16" :stroke-width="1.75" />添加投标人</el-button>
            <el-button :disabled="blocked" @click="folders?.click()"><FolderOpen :size="16" :stroke-width="1.75" />导入文件夹</el-button>
          </div>
        </div>
        <div v-if="adding" class="si-card adding">
          <el-input v-model="newName" maxlength="255" placeholder="投标人名称（以投标函上的全称为准）" aria-label="新投标人名称" autofocus @keyup.enter="addBidder" @keyup.esc="adding = false" />
          <el-button type="primary" :disabled="blocked || !newName.trim()" @click="addBidder">添加</el-button>
          <el-button text @click="adding = false; newName = ''">取消</el-button>
        </div>

        <div v-if="!project.bidders.length" class="si-drop hero" :class="{ dragging: dragging === 'bulk' }" :aria-disabled="blocked" role="button" tabindex="0"
             @click="folders?.click()" @keydown.enter="folders?.click()" @dragover.prevent="dragging = 'bulk'" @dragleave.self="dragging = null" @drop.prevent="drop($event, undefined, true)">
          <FolderOpen :size="28" :stroke-width="1.5" />
          <p class="si-drop-title">把各家的投标文件夹一起拖进来</p>
          <p class="hint">每个文件夹建一家投标人，上传前可核对名称；也可以先“添加投标人”，再逐家加入文件。</p>
        </div>
        <template v-else>
          <div class="cards">
            <BidderCard v-for="bidder in project.bidders" :key="bidder.id" :project-id="id" :bidder="bidder" :files="filesOf(bidder.id)" :disabled="blocked" :busy="project.busy" :row-errors="rowErrors"
                        @rename="name => renameBidder(bidder.id, name)" @remove="removeBidder(bidder.id)" @pick="choose(bidder.id)" @drop="e => drop(e, bidder.id)"
                        @password="setPassword" @remove-file="removeFile" />
          </div>
          <div class="si-drop slim" :class="{ dragging: dragging === 'bulk' }" :aria-disabled="blocked" role="button" tabindex="0"
               @click="folders?.click()" @keydown.enter="folders?.click()" @dragover.prevent="dragging = 'bulk'" @dragleave.self="dragging = null" @drop.prevent="drop($event, undefined, true)">
            <FolderOpen :size="18" :stroke-width="1.75" /><p class="si-drop-title">拖入更多投标人的文件夹</p>
          </div>
        </template>
        <footer class="step-foot start">
          <span v-if="startBlock" class="hint">{{ startBlock }}</span>
          <span v-else-if="project.bidders.length < 2" class="hint">只有 1 家时只做符合性检查，不比对串标线索</span>
          <el-button v-if="!locked" type="primary" :disabled="!!startBlock || pending" @click="startCheck">开始初审</el-button>
          <el-button v-else @click="step = 2">查看初审结果</el-button>
        </footer>
        <p class="hint limits num">单个文件 {{ project.limits.file_mb }} MB 以内 · PDF 不超过 {{ project.limits.pdf_pages }} 页 · 全项目扫描页与图片合计 {{ project.limits.ocr_pages }} 页 · 至少 2 家才比对串标线索</p>
      </div>

      <div v-else-if="step === 2 && (project.stage === 'checking' || project.stage === 'checked' || project.check?.state === 'failed')" class="result">
        <p class="result-status"><BidStatus v-if="project.stage === 'checking'" tone="run" label="正在本地检查：混装、联系信息、文件属性、相同图片" />
          <BidStatus v-else-if="project.check?.state === 'failed'" tone="warn" :label="project.check.error ?? '本地检查失败'" />
          <BidStatus v-else tone="ok" label="本地检查一已完成；相似段落、报价与符合项检查随后补上" /></p>
        <BidClues v-if="project.stage === 'checked'" :project-id="id" :bidders="project.bidders" :files="project.files" :state-key="checkKey" />
      </div>
      <EmptyState v-else-if="step === 2" :icon="ScanSearch" title="还没有初审结果" description="确认要求清单、放齐投标文件后开始初审，这里会列出总览、要求响应、各家问题、串标线索与报价分析。" />
      <EmptyState v-else :icon="FileDown" title="还不能导出" description="初审完成并逐条核对后，在这里导出交给评标委员会的《初审疑点报告》（Word）。" />

      <footer class="page-foot"><p class="declaration hint">本系统提供初审线索，不构成对串通投标或否决投标的认定，由评标委员会核实后决定。</p></footer>
    </template>

    <input ref="picker" type="file" class="file-input" multiple :accept="target ? '.docx,.doc,.wps,.rtf,.pdf,.xlsx,.xls,.et,.jpg,.jpeg,.png' : '.docx,.doc,.wps,.rtf,.pdf'" @change="picked">
    <input ref="folders" type="file" class="file-input" multiple webkitdirectory @change="pickedFolders">
    <BidSettingsDialog v-if="project" v-model="settingsDialog" title="项目设置" confirm-text="保存" :initial="{ title: project.title, opening_date: project.opening_date, policy: project.policy }" :busy="pending" :error="settingsError" @submit="saveSettings" />
    <el-dialog v-model="groupDialog" title="核对投标人分组" width="600px" :close-on-click-modal="!pending" append-to-body>
      <p class="dialog-lede">每个文件夹对应一家投标人。请把名称改成投标函上的全称；同名的已有投标人会把文件并入该卡片。</p>
      <ul class="group-list">
        <li v-for="group in grouped" :key="group.original">
          <span class="folder"><FolderOpen :size="14" :stroke-width="1.75" aria-hidden="true" /><span :title="group.original">{{ group.original }}</span></span>
          <el-input v-model="group.name" maxlength="255" placeholder="投标人名称" :aria-label="`${group.original} 的投标人名称`" :disabled="pending" />
          <span class="hint num">{{ group.files.length }} 份</span>
        </li>
      </ul>
      <p v-if="groupError" class="error" role="alert">{{ groupError }}</p>
      <template #footer><el-button :disabled="pending" @click="groupDialog = false">取消</el-button><el-button type="primary" :loading="pending" @click="confirmGroups">{{ percent !== null ? `正在上传 ${percent}%` : '按分组上传' }}</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.breadcrumb { display: flex; gap: 8px; margin-bottom: 16px; font: var(--si-font-meta); color: var(--si-ink-3); }
.breadcrumb a { color: var(--si-ink-3); text-decoration: none; }
.breadcrumb a:hover { color: var(--si-ink-1); }
.breadcrumb span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.title { min-width: 0; }
h1 { margin: 0; font: var(--si-font-title); color: var(--si-ink-1); overflow-wrap: anywhere; }
.meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 6px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.meta i { font-style: normal; color: var(--si-ink-5); }
.head-actions { flex: none; display: flex; align-items: center; gap: 8px; }
.head-actions .el-button svg { margin-right: 6px; }
:global(.danger-item) { color: var(--si-danger-ink) !important; }

.steps { display: flex; align-items: center; gap: 4px; margin: 24px 0 28px; padding: 10px 0; border-top: 1px solid var(--si-hair); border-bottom: 1px solid var(--si-hair); }
.steps button { display: flex; align-items: center; gap: 10px; padding: 6px 14px 6px 0; margin-right: 24px; border: 0; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); cursor: pointer; }
.steps button:hover { color: var(--si-ink-1); }
.number { width: 24px; height: 24px; box-sizing: border-box; border: 1px solid var(--si-hair-strong); border-radius: 50%; display: grid; place-items: center; font: var(--si-font-code-sm); }
.steps .current { color: var(--si-ink-1); font-weight: 600; }
.steps .current .number { background: var(--si-brand); border-color: var(--si-brand); color: var(--si-white); }
.steps .done { color: var(--si-ink-2); }
.steps .done .number { background: var(--si-surface-2); border-color: transparent; color: var(--si-ink-2); }
.progress { margin-left: auto; }
.alert { margin-bottom: 20px; }

.section-head { display: flex; align-items: baseline; justify-content: space-between; padding-bottom: 10px; margin-bottom: 4px; border-bottom: 1px solid var(--si-hair-strong); }
.section-head .si-overline { margin: 0; }
.tender { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 24px 48px; }
.tender .si-drop, .tender-ready .si-drop { margin-top: 16px; }
.tender-ready { display: flex; flex-direction: column; gap: 32px; }
.tender-ready .step-foot { padding-top: 0; border-top: 0; }
.si-drop.slim { flex-direction: row; justify-content: center; gap: 10px; padding: 14px 16px; }
.si-drop.slim .si-drop-title { margin: 0; font: var(--si-font-label); color: var(--si-ink-2); }
.si-drop[aria-disabled="true"] { opacity: .65; cursor: progress; }
.next-lede { margin: 12px 0 8px; font: var(--si-font-body); color: var(--si-ink-2); }
.groups { margin: 0 0 12px; padding: 0; list-style: none; }
.groups li { display: flex; align-items: center; gap: 8px; padding: 9px 0; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-3); }
.groups li span { margin-left: auto; color: var(--si-ink-5); }
.step-foot { grid-column: 1 / -1; display: flex; justify-content: flex-end; padding-top: 20px; border-top: 1px solid var(--si-hair); }

.toolbar { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding-bottom: 10px; margin-bottom: 16px; border-bottom: 1px solid var(--si-hair-strong); }
.counts { display: flex; align-items: baseline; gap: 16px; }
.counts .si-overline { margin: 0; }
.tools { display: flex; gap: 8px; }
.tools .el-button svg { margin-right: 6px; }
.adding { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; padding: 12px 16px; }
.adding .el-input { max-width: 420px; }
.cards { display: flex; flex-direction: column; gap: 16px; }
.bids .si-drop.slim { margin-top: 16px; }
.si-drop.hero { padding: 56px 16px; }
.limits { margin: 16px 0 0; }
.step-foot.start { align-items: center; gap: 16px; margin-top: 24px; }
.result-status { margin: 0 0 24px; }

/* 页面至少占满主区域，声明贴底（内容长时随内容下移，至少留 48px） */
.bid-project { display: flex; flex-direction: column; min-height: 100%; box-sizing: border-box; padding-bottom: 20px; }  /* 底边与侧栏底栏的账号行齐平 */
.page-foot { margin-top: auto; padding-top: 48px; }
.declaration { margin: 0; padding-top: 16px; border-top: 1px solid var(--si-hair); }
.file-input { display: none; }
.dialog-lede { margin: 0 0 16px; font: var(--si-font-body); color: var(--si-ink-2); }
.group-list { margin: 0; padding: 0; list-style: none; }
.group-list li { display: grid; grid-template-columns: 160px minmax(0, 1fr) 48px; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--si-hair-faint); }
.folder { display: flex; align-items: center; gap: 6px; min-width: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.folder span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-list .hint { text-align: right; }
.error { margin: 12px 0 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
