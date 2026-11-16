<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { UploadCloud } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidStageLabel, type BidProject, type BidSettings } from '@/api/bid'
import BidFiles from '@/components/bid/BidFiles.vue'

const route = useRoute(), router = useRouter(), id = computed(() => String(route.params.id))
const project = ref<BidProject>(), loading = ref(false), error = ref(''), pending = ref(false), percent = ref<number | null>(null)
const step = ref(0), titles = ['招标文件与要求', '投标文件', '初审结果', '导出']
const form = reactive<BidSettings>({ title: '', opening_date: '', policy: 'local_only' })
const blocked = computed(() => pending.value)  // 解析期间仍可继续加文件、改名称，新文件由同一次解析接着处理
const tenderFiles = computed(() => project.value?.files.filter(f => !f.bidder_id) ?? [])
const bidderNames = reactive<Record<string, string>>({}), newName = ref('')
const rowErrors = reactive<Record<string, string>>({})  // 密码错误等提示显示在对应文件行，不只在页顶
const picker = ref<HTMLInputElement>(), folders = ref<HTMLInputElement>(), target = ref<string>()
const dragging = ref(false), groupDialog = ref(false)
const grouped = ref<{ original: string; name: string; files: File[] }[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0, edits = 0  // edits：轮询结果晚于修改返回时丢弃，避免旧状态覆盖新状态
function resetTimer() { if (timer) clearTimeout(timer); timer = undefined }
function accept(data: BidProject) {
  project.value = data
  for (const b of data.bidders) if (!(b.id in bidderNames)) bidderNames[b.id] = b.name
  resetTimer()
  if (data.busy) timer = setTimeout(() => void load(false), 1500)
}
async function load(initial: boolean) {
  const current = generation, edit = edits
  if (initial) loading.value = true
  try {
    const data = await bidApi.get(id.value)
    if (current !== generation || edit !== edits) return
    accept(data)
    if (initial) Object.assign(form, { title: data.title, opening_date: data.opening_date, policy: data.policy })
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
async function save() { await mutate(() => bidApi.update(id.value, form)) }
async function removeFile(fid: string) { await mutate(() => bidApi.removeFile(id.value, fid)) }
async function setPassword(fid: string, password: string) {
  delete rowErrors[fid]
  await mutate(() => bidApi.password(id.value, fid, password))
  if (error.value) { rowErrors[fid] = error.value; error.value = '' }
}
async function reparse() { await mutate(() => bidApi.parse(id.value)) }
async function renameBidder(bidder: string) { await mutate(() => bidApi.renameBidder(id.value, bidder, bidderNames[bidder]!)) }
async function removeBidder(bidder: string) { await mutate(() => bidApi.removeBidder(id.value, bidder)) }
async function addBidder() {
  if (!newName.value.trim()) return
  await mutate(() => bidApi.addBidder(id.value, newName.value.trim()))
  if (!error.value) newName.value = ''
}
function choose(bidder?: string) { if (!blocked.value) { target.value = bidder; picker.value?.click() } }
async function upload(files: File[], bidder?: string, groups?: string[]) {
  if (!files.length) return
  if (!project.value) return
  if (files.some(f => f.size > project.value!.limits.file_mb * 1024 * 1024)) { error.value = `单文件不能超过 ${project.value.limits.file_mb} MB`; return }
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
    const parts = paths[index]!, name = parts.length > 1 ? parts[unwrap ? 1 : 0]! : '待填写投标人'
    if (!map.has(name)) map.set(name, [])
    map.get(name)!.push(item.file)
  })
  grouped.value = Array.from(map, ([name, files]) => ({ original: name, name, files })); groupDialog.value = true
}
function pickedFolders(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; prepareGroups(files.map(file => ({ file, path: file.webkitRelativePath || file.name })))
}
async function drop(event: DragEvent, bidder?: string, folderMode = false) {
  dragging.value = false
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
  } catch { error.value = '文件夹读取失败，请使用“选择文件夹”或逐卡上传' }
}
async function confirmGroups() {
  const names = grouped.value.map(g => g.name.trim())
  if (names.some(n => !n) || new Set(names).size !== names.length) { error.value = '请为每家填写不同的投标人名称'; return }
  const files: File[] = [], groups: string[] = []
  grouped.value.forEach((g, i) => g.files.forEach(f => { files.push(f); groups.push(names[i]!) }))
  await upload(files, undefined, groups)
  if (!error.value) { groupDialog.value = false; grouped.value = [] }
}
async function removeProject() {
  if (!project.value || blocked.value) return
  try { await ElMessageBox.confirm('将删除本项目的全部原件、密码与记录，确定删除？', '删除初审项目', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  pending.value = true
  try { await bidApi.remove(id.value); await router.push('/bid') }
  catch (e) { error.value = e instanceof ApiError ? e.message : '删除失败' }
  finally { pending.value = false }
}
</script>

<template>
  <div class="page page--wide bid-project">
    <div class="breadcrumb"><router-link to="/bid">标书初审</router-link><span> / 项目</span></div>
    <p v-if="loading" role="status" class="hint">正在读取项目…</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="project">
      <header><div><h1>{{ project.title }}</h1><p class="hint">开标日期 {{ project.opening_date }} · {{ bidStageLabel[project.stage] }} · 仅本人可见</p></div><el-button text :disabled="blocked" @click="removeProject">删除项目</el-button></header>
      <nav class="bid-steps" aria-label="标书初审步骤"><button v-for="(title, index) in titles" :key="title" :class="{ selected: step === index }" :aria-current="step === index ? 'step' : undefined" @click="step = index"><span class="step-number">{{ index + 1 }}</span>{{ title }}</button></nav>
      <p v-if="percent !== null" class="hint" role="status">正在上传 {{ percent }}%</p>
      <p v-else-if="project.busy" class="hint" role="status">正在逐个解析文件，可以继续加入其他投标人的文件…</p>
      <el-alert v-if="project.error_message" :title="project.error_message" type="warning" :closable="false" />

      <template v-if="step === 0">
        <section class="settings">
          <el-form label-position="top" @submit.prevent="save"><div class="form-grid"><el-form-item label="项目名称"><el-input v-model="form.title" maxlength="255" :disabled="blocked" /></el-form-item><el-form-item label="开标日期"><el-date-picker v-model="form.opening_date" value-format="YYYY-MM-DD" :clearable="false" :disabled="blocked" /></el-form-item></div>
            <el-form-item label="外发策略"><el-radio-group v-model="form.policy" :disabled="blocked"><el-radio value="local_only">只做本地检查</el-radio><el-radio value="ai">确认脱敏片段后由模型核对</el-radio></el-radio-group></el-form-item>
            <el-button type="primary" :disabled="blocked || !form.title.trim() || !form.opening_date" @click="save">保存项目信息</el-button>
          </el-form>
        </section>
        <section><h2>招标文件 <span class="hint num">{{ tenderFiles.length }} / {{ project.limits.tender_files }}</span></h2>
          <div class="si-drop" :aria-disabled="blocked" role="button" tabindex="0" @click="choose()" @keydown.enter="choose()" @dragover.prevent @drop.prevent="drop($event)"><UploadCloud :size="22" :stroke-width="1.75" /><p class="si-drop-title">拖入招标文件，或点击选择</p><p class="hint">Word、PDF；正文、答疑、补遗与澄清可分别加入。</p></div>
          <BidFiles :project-id="id" :files="tenderFiles" :disabled="blocked" :row-errors="rowErrors" @remove="removeFile" @password="setPassword" />
          <p class="hint next-note">下一步将从招标文件整理关键参数与要求清单；本页已保存原件与解析结果。</p>
          <el-button @click="step = 1">整理投标文件 →</el-button>
        </section>
      </template>

      <template v-else-if="step === 1">
        <div class="group-actions"><el-input v-model="newName" maxlength="255" placeholder="投标人名称" aria-label="新投标人名称" :disabled="blocked" @keyup.enter="addBidder" /><el-button :disabled="blocked || !newName.trim()" @click="addBidder">添加投标人</el-button><el-button :disabled="blocked" @click="folders?.click()">选择文件夹</el-button><el-button :disabled="blocked || !project.files.length" @click="reparse">重新解析</el-button></div>
        <div class="si-drop bulk" :class="{ dragging }" :aria-disabled="blocked" @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="drop($event, undefined, true)"><UploadCloud :size="22" :stroke-width="1.75" /><p class="si-drop-title">把多家投标人的文件夹一起拖到这里</p><p class="hint">按文件夹建卡，先核对名称再上传；也可以在下方各卡片加入文件。</p></div>
        <p class="hint limits">投标文件 {{ project.files.filter(f => f.bidder_id).length }} / {{ project.limits.bid_files }} 份 · 单文件 {{ project.limits.file_mb }} MB · PDF {{ project.limits.pdf_pages }} 页 · 项目扫描页及图片 {{ project.limits.ocr_pages }} 页</p>
        <div v-if="!project.bidders.length" class="hint">先添加投标人，或拖入按投标人整理的文件夹。</div>
        <section v-for="bidder in project.bidders" :key="bidder.id" class="si-card bidder" @dragover.prevent @drop.prevent="drop($event, bidder.id)">
          <div class="bidder-head"><span class="code">{{ bidder.code }}</span><el-input v-model="bidderNames[bidder.id]" maxlength="255" :aria-label="`投标人 ${bidder.code} 名称`" :disabled="blocked" /><el-button text :disabled="blocked || !bidderNames[bidder.id]?.trim() || bidderNames[bidder.id] === bidder.name" @click="renameBidder(bidder.id)">保存名称</el-button><el-button :disabled="blocked" @click="choose(bidder.id)">加入文件</el-button><el-button text :disabled="blocked || project.files.some(f => f.bidder_id === bidder.id)" @click="removeBidder(bidder.id)">移除投标人</el-button></div>
          <BidFiles :project-id="id" :files="project.files.filter(f => f.bidder_id === bidder.id)" :disabled="blocked" :row-errors="rowErrors" @remove="removeFile" @password="setPassword" />
          <p v-if="!project.files.some(f => f.bidder_id === bidder.id)" class="hint">将本家的投标文件拖到这张卡片，或点击“加入文件”。</p>
        </section>
      </template>
      <section v-else class="future"><h2>{{ step === 2 ? '初审结果' : '导出初审疑点报告' }}</h2><p class="hint">{{ step === 2 ? '完成要求清单与初审后，在此查看问题、响应矩阵与串标线索。' : '初审与人工核对完成后，在此导出交给评标委员会的报告。' }}</p></section>
      <p class="declaration hint">本系统提供初审线索，不构成对串通投标或否决投标的认定，由评标委员会核实后决定。</p>
    </template>
    <input ref="picker" type="file" class="file-input" multiple :accept="target ? '.docx,.doc,.wps,.rtf,.pdf,.xlsx,.xls,.et,.jpg,.jpeg,.png' : '.docx,.doc,.wps,.rtf,.pdf'" @change="picked">
    <input ref="folders" type="file" class="file-input" multiple webkitdirectory @change="pickedFolders">
    <el-dialog v-model="groupDialog" title="核对投标人分组" width="600px" :close-on-click-modal="!pending">
      <p class="hint">每个文件夹对应一家投标人。请核对名称与文件数量。</p>
      <div v-for="group in grouped" :key="group.original" class="group-line"><el-input v-model="group.name" maxlength="255" :aria-label="`${group.original} 的投标人名称`" :disabled="pending" /><span class="hint">{{ group.files.length }} 份文件</span></div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><el-button :disabled="pending" @click="groupDialog = false">取消</el-button><el-button type="primary" :loading="pending" :disabled="blocked" @click="confirmGroups">按分组上传</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.breadcrumb { font: var(--si-font-meta); color: var(--si-ink-3); margin-bottom: 20px; }
header { display: flex; align-items: start; justify-content: space-between; gap: 24px; }h1 { font: var(--si-font-title); margin: 0; }header p { margin: 8px 0 24px; }
.bid-steps { display: flex; margin: 0 0 24px; padding: 12px 0; border-top: 1px solid var(--si-hair); border-bottom: 1px solid var(--si-hair); }.bid-steps button { flex: 1; display: flex; align-items: center; gap: 10px; border: 0; background: transparent; color: var(--si-ink-3); font: var(--si-font-label); padding: 0; cursor: pointer; }.bid-steps .selected { color: var(--si-ink-1); font-weight: 600; }.step-number { width: 24px; height: 24px; border: 1px solid var(--si-hair-strong); border-radius: 50%; display: grid; place-items: center; font: var(--si-font-code-sm); }.selected .step-number { background: var(--si-brand); color: var(--si-white); border-color: var(--si-brand); }
section { margin: 28px 0; }h2 { font: var(--si-font-h3); margin: 0 0 12px; display: flex; gap: 12px; align-items: baseline; }
.settings { padding-bottom: 28px; border-bottom: 1px solid var(--si-hair); }.form-grid { display: grid; grid-template-columns: minmax(240px, 1fr) 260px; gap: 24px; max-width: 800px; }
.group-actions { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }.group-actions > .el-input { width: 260px; }.bulk { padding: 20px; }.limits { margin: 12px 0 24px; }
.bidder { padding: 20px; }.bidder-head { display: flex; align-items: center; gap: 12px; }.bidder-head > .el-input { max-width: 340px; flex: 1; }.code { width: 28px; flex: none; font: var(--si-font-code); color: var(--si-ink-3); }
.next-note { margin: 20px 0; }.file-input { display: none; }.declaration { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--si-hair); }.future { min-height: 160px; }
.group-line { display: flex; align-items: center; gap: 16px; margin: 12px 0; }.group-line > .el-input { flex: 1; }.group-line span { flex: none; }.error { color: var(--si-danger-ink); }
.si-drop[aria-disabled="true"] { opacity: .65; cursor: progress; }
</style>
