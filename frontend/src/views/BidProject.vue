<script setup lang="ts">
import SiNotice from '@/components/ui/SiNotice.vue'
// 标书初审 · 项目页（澄明 8.5 ②）：页头（面包屑、项目名、信息徽标、操作）→ 招标文件卡 → 一行进度（只在这一处）→ 投标人卡片网格
// → 检查中只显示进度、不出结论 → 结果出来后主从两栏（问题清单 + 证据）。条款与各家文件在右侧抽屉里看和改。
import { computed, nextTick, onBeforeUnmount, provide, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { BookOpen, Calendar, ChevronLeft, ChevronRight, Download, FolderOpen, ListChecks, Lock, Play, Plus, RefreshCw, Sparkle, Tags, UploadCloud, Users } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { reportApi } from '@/api/report'
import { useAuthStore } from '@/stores/auth'
import { bidTrip } from '@/utils/trip'
import { bidApi, type BidCategory, type BidCheck, type BidFolder, type BidOpeningPriceInput, type BidProject, type BidSettings, type BidSigning, type BidSignMark } from '@/api/bid'
import RowMore from '@/components/RowMore.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import BidFiles from '@/components/bid/BidFiles.vue'
import BidderCard from '@/components/bid/BidderCard.vue'
import BidderTile from '@/components/bid/BidderTile.vue'
import BidSignSheets from '@/components/bid/BidSignSheets.vue'
import { bidResultsKey, openingError, shortNames, type BidResultsContext } from '@/components/bid/results'
import { matchInvitee, nameKey } from '@/components/bid/invitees'
import { folderPath } from '@/components/bid/folders'
import BidSettingsDialog from '@/components/bid/BidSettingsDialog.vue'
import BidRequirements from '@/components/bid/BidRequirements.vue'
import BidResults from '@/components/bid/BidResults.vue'
import BidSentDrawer from '@/components/bid/BidSentDrawer.vue'

const route = useRoute(), router = useRouter(), id = computed(() => String(route.params.id))
const auth = useAuthStore()
// 分类名与文件夹路径只用于页头显示（V34-B-01a）
const categories = ref<BidCategory[]>([]), folderList = ref<BidFolder[]>([])
void bidApi.categories().then(r => { categories.value = r.items }).catch(() => {})
void bidApi.folders().then(r => { folderList.value = r.folders }).catch(() => {})
const categoryName = computed(() => categories.value.find(c => c.id === project.value?.category_id)?.name)
const folderName = computed(() => project.value?.folder_id ? folderPath(folderList.value, project.value.folder_id) : '')
const project = ref<BidProject>(), loading = ref(false), error = ref(''), pending = ref(false), percent = ref<number | null>(null)
const check = ref<BidCheck>(), notice = ref('')  // 初审结果：结果栏、投标人卡片与抽屉里的签章页共用
const phase = computed<'materials' | 'checking' | 'results'>(() => project.value?.stage === 'checked' ? 'results' : project.value?.stage === 'checking' ? 'checking' : 'materials')
const locked = computed(() => phase.value !== 'materials')  // 开始检查后材料锁定
const blocked = computed(() => pending.value || locked.value)  // 读取期间仍可继续加文件、改名称，新文件由同一次读取接着处理
const tenderFiles = computed(() => project.value?.files.filter(f => !f.bidder_id) ?? [])
const bidFiles = computed(() => project.value?.files.filter(f => f.bidder_id) ?? [])
const filesOf = (bidder: string) => project.value?.files.filter(f => f.bidder_id === bidder) ?? []
const rowErrors = reactive<Record<string, string>>({})  // 密码错误等提示显示在对应文件行，不只在页顶
const sentOpen = ref(false), exporting = ref(false)
const tenderOpen = ref(false), bidderOpen = ref(''), selected = ref('')
const picker = ref<HTMLInputElement>(), folders = ref<HTMLInputElement>(), target = ref<string>(), targetName = ref<string>()
// 受邀单位名单（V34-B-01b）：名单中尚无分组的单位显示为虚线卡，拖入文件时才建分组；已有分组只是简称时提示改用全称
const invitees = computed(() => project.value?.invitees ?? [])
const bidderKeys = computed(() => new Set(project.value?.bidders.map(b => nameKey(b.name)) ?? []))
const suggestions = computed(() => {
  const out: Record<string, string> = {}
  for (const b of project.value?.bidders ?? []) {
    if (invitees.value.some(n => nameKey(n) === nameKey(b.name))) continue
    const full = matchInvitee(b.name, invitees.value)
    if (full && !bidderKeys.value.has(nameKey(full))) out[b.id] = full
  }
  return out
})
const waiting = computed(() => {
  const claimed = new Set(Object.values(suggestions.value).map(nameKey))
  return locked.value ? [] : invitees.value.filter(n => !bidderKeys.value.has(nameKey(n)) && !claimed.has(nameKey(n)))
})
const overWaiting = ref<string | null>(null), overAdd = ref(false), overTender = ref(false)
const groupDialog = ref(false), settingsDialog = ref(false), settingsError = ref('')
const grouped = ref<{ original: string; name: string; files: File[] }[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0, edits = 0  // edits：轮询结果晚于修改返回时丢弃，避免旧状态覆盖新状态

function resetTimer() { if (timer) clearTimeout(timer); timer = undefined }
function accept(data: BidProject) {
  project.value = data
  resetTimer()
  // 扫描页延后识别：识别完成前继续轮询，结果随识别补上
  const ocr = data.check?.ocr
  if (data.busy || data.stage === 'checking' || (ocr && ocr.total > ocr.done) || data.check.ocr_result?.state === 'updating') timer = setTimeout(() => void load(false), data.busy || data.stage === 'checking' ? 1500 : 5000)
}
async function load(initial: boolean) {
  const current = generation, edit = edits
  if (initial) loading.value = true
  try {
    const data = await bidApi.get(id.value)
    if (current !== generation || edit !== edits) return
    accept(data)
  } catch (e) { if (current === generation) error.value = e instanceof ApiError ? e.message : '项目读取失败' }
  finally { if (current === generation) loading.value = false }
}
watch(id, () => {
  generation++; resetTimer(); project.value = undefined; check.value = undefined; error.value = ''
  grouped.value = []; groupDialog.value = false; tenderOpen.value = false; bidderOpen.value = ''; selected.value = ''
  void load(true)
}, { immediate: true })
onBeforeUnmount(() => { generation++; resetTimer() })
async function mutate(action: () => Promise<BidProject>, whenLocked = false, inline = false) {
  if (pending.value || (locked.value && !whenLocked)) return
  pending.value = true; error.value = ''; resetTimer(); edits++
  const current = generation
  try { const data = await action(); if (current === generation) accept(data) }
  catch (e) {
    if (current !== generation) return
    error.value = e instanceof ApiError ? e.message : '操作失败，请重试'
    // 投标人抽屉盖住了页面顶部的错误条，抽屉里的操作失败时另弹提示（行内显示错误的除外）
    if (drawerBidder.value && !inline) ElMessage.error(error.value)
  }
  finally { pending.value = false }
}

// ---- 初审结果：项目页读取一次，结果栏、投标人卡片与抽屉里的签章页共用（V35-F-05）
const checkKey = computed(() => {
  const p = project.value
  return [p?.stage, p?.check.state, p?.check.ocr.done, p?.check.ocr_result?.state, p?.check.ocr_result?.version, p?.model.state, p?.model.done].join(':')
})
const modelKey = computed(() => `${project.value?.model?.state}:${project.value?.model?.done}`)
let saving = 0, checkRequest = 0
async function loadCheck() {
  if (phase.value !== 'results') return
  const current = ++checkRequest
  const projectAtRequest = generation
  try { const next = await bidApi.check(id.value); if (current === checkRequest && projectAtRequest === generation && !saving) check.value = next }
  catch (e) { if (current === checkRequest && projectAtRequest === generation) notice.value = e instanceof ApiError ? e.message : '检查结果读取失败' }
}
watch(checkKey, () => {
  if (phase.value === 'results') void loadCheck()
  else { checkRequest++; check.value = undefined }
}, { immediate: true })
const counts = computed(() => Object.fromEntries((check.value?.conclusion.bidders ?? []).map(b => [b.id, b])))
const names = computed(() => Object.fromEntries((project.value?.bidders ?? []).map(b => [b.id, b.name])))
const shorts = computed(() => shortNames(project.value?.bidders ?? []))
const codes = computed(() => Object.fromEntries((project.value?.bidders ?? []).map(b => [b.id, b.code.replace(/[（）()]/g, '')])))
const fileNames = computed(() => Object.fromEntries((project.value?.files ?? []).map(f => [f.id, f.name])))
const renderable = computed(() => Object.fromEntries((project.value?.files ?? []).map(f => [f.id, !f.original_deleted && ['pdf', 'jpg', 'jpeg', 'png'].includes(f.ext)])))
const ctx: BidResultsContext = { get projectId() { return id.value }, data: check, names, shorts, codes, fileNames, renderable, dismiss, markSigning, openPage }
provide(bidResultsKey, ctx)
async function dismiss(key: string, dismissed: boolean) {
  if (pending.value) return
  notice.value = ''; saving++
  try { check.value = await bidApi.dismiss(id.value, key, dismissed) }
  catch (e) { notice.value = e instanceof ApiError ? e.message : '没有保存，请重试' }
  finally { saving-- }
}
async function markSigning(pages: BidSigning[], mark: BidSignMark | null) {
  if (!pages.length || pending.value) return
  const before = pages.map(s => [s, s.mark] as const)
  pages.forEach(s => { s.mark = mark }); notice.value = ''; saving++
  try {
    if (pages.length === 1) await bidApi.sign(id.value, pages[0]!, mark)
    else await bidApi.signBatch(id.value, pages.map(s => ({ file_id: s.file_id, page: s.page, loc: s.loc })), mark)
    check.value = await bidApi.check(id.value)  // 缺章是严重问题，结论随之更新
  } catch (e) {
    before.forEach(([s, m]) => { s.mark = m })
    notice.value = e instanceof ApiError ? e.message : '标注没有保存，已恢复，请重试'
  } finally { saving-- }
}
// 查看原件页：大图对话框，可翻页；loc 只框在打开的那一页
const page = ref<{ fileId: string; page: number; loc: string | null; first: number } | null>(null)
const pageCount = computed(() => project.value?.files.find(f => f.id === page.value?.fileId)?.page_count ?? 1)
function openPage(fileId: string, n: number, loc?: string | null) { page.value = { fileId, page: n, loc: loc ?? null, first: n } }

// 工具条（规范第二节）：本机四站；发起模型补查后接上遮住 · 复检 · 模型补查 · 还原。
const trip = computed(() => {
  const p = project.value
  if (!p) return null
  const reading = p.busy && p.files.length ? { done: p.files.filter(f => f.state !== 'waiting').length, total: p.files.length } : undefined
  return bidTrip({ status: p.status, stage: p.stage, progress: p.check.progress, checkFailed: p.check.state === 'failed',
    upload: percent.value, reading, ocr: p.check.ocr, ocrState: p.check.ocr_result?.state, hasMaterials: !!tenderKey.value,
    modelState: phase.value === 'results' ? p.model?.state : null })
})
// 完成徽标的用时：本机检查各步用时之和
const tripSeconds = computed(() => {
  const e = check.value?.elapsed
  return e ? Object.values(e).reduce((n, v) => n + v, 0) : null
})
// 补查等你确认或失败时，工具条上的按钮带到结果区的模型补查卡
function toModelCheck() { document.querySelector('.bidp .ai-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }
const conclusion = computed(() => phase.value === 'results' ? check.value?.conclusion : undefined)
const preliminary = computed(() => ['scanning', 'updating', 'failed', 'expired'].includes(check.value?.ocr_result?.state ?? ''))

// ---- 招标文件卡：收起时一行说明
const tenderKey = computed(() => tenderFiles.value.filter(f => f.state === 'done').map(f => f.id).join(','))
const tender = computed(() => {
  const p = project.value, list = tenderFiles.value
  if (!p || !list.length) return null
  const failed = list.filter(f => f.state === 'failed').length, reading = list.filter(f => f.state === 'waiting').length
  const key = p.requirements.key ?? p.requirements.enabled
  if (failed) return { badge: { tone: 'mid', text: `${failed} 份待处理` }, line: `${list.length} 份 · 有文件需要密码或读取失败` }
  if (reading) return { badge: { tone: 'info', text: '读取中' }, line: `${list.length} 份 · 正在本机读取` }
  if (p.stage === 'preparing') return { badge: { tone: 'mid', text: '条款待确认' }, line: `${list.length} 份 · 已读取 · ★ 与否决条款 ${key} 条，请核对后确认` }
  return { line: `${list.length} 份 · 已读取 · ★ 与否决条款 ${key} 条` }
})

// ---- 操作
function openSettings() { settingsError.value = ''; settingsDialog.value = true }
async function saveSettings(body: BidSettings) {
  await mutate(() => bidApi.update(id.value, body), true)  // 开始检查后仍可改分类与文件夹
  if (error.value) { settingsError.value = error.value; error.value = '' } else settingsDialog.value = false
}
// 只在本机处理的项目检查完后改为可选模型补查：不退回修改材料；补查卡随后出现在问题清单，发送前仍逐段确认
async function enableModel() {
  const p = project.value
  if (!p || pending.value) return
  try { await ElMessageBox.confirm('改为“本机检查，可选模型补查”后，★ 与否决条款中本机未找到的，可以交给模型补查。发送前会逐段列出遮住后的片段，由你确认才发送；不确认就不会发送任何内容。', '开启模型补查', { confirmButtonText: '开启', cancelButtonText: '取消' }) }
  catch { return }
  await mutate(() => bidApi.update(id.value, { title: p.title, opening_date: p.opening_date, policy: 'ai', category_id: p.category_id, folder_id: p.folder_id }), true)
  if (error.value) return
  await loadCheck()
  ElMessage.success(check.value?.conclusion?.lookup ? '已开启，问题清单底部可交模型补查' : '已开启。本机已找到全部 ★ 与否决条款，暂时没有需要模型补查的')
}
async function removeFile(fid: string) { delete rowErrors[fid]; await mutate(() => bidApi.removeFile(id.value, fid)) }
async function setPassword(fid: string, password: string) {
  delete rowErrors[fid]
  await mutate(() => bidApi.password(id.value, fid, password), false, true)
  if (error.value) { rowErrors[fid] = error.value; error.value = '' }
}
// 开始检查：条款清单确认、文件读取完、至少一家有投标文件
const startBlock = computed(() => {
  const p = project.value
  if (!p || locked.value) return ''
  if (!tenderFiles.value.length) return '先加入招标文件'
  if (p.stage !== 'ready') return '先核对并确认招标文件的条款'
  if (p.busy || p.files.some(f => f.state === 'waiting')) return '文件还在读取'
  if (!bidFiles.value.some(f => f.state === 'done')) return '再加入投标文件'
  return p.bidders.length < 2 ? '只有 1 家时只做符合性检查' : ''
})
const canStart = computed(() => !!project.value && project.value.stage === 'ready' && !pending.value && (!startBlock.value || startBlock.value.startsWith('只有')))
async function startCheck() { startGlow.value = false; await mutate(() => bidApi.startCheck(id.value)); selected.value = '' }
// 条款清单确认后关掉抽屉，“开始检查”按钮亮几下光晕，提示下一步（还不能开始时由按钮旁的说明提示缺什么）
const startGlow = ref(false)
let glowTimer = 0
async function termsConfirmed() {
  tenderOpen.value = false
  await load(false)
  await nextTick()
  if (!canStart.value) return
  window.clearTimeout(glowTimer)
  startGlow.value = true
  glowTimer = window.setTimeout(() => { startGlow.value = false }, 3600)
}
onBeforeUnmount(() => window.clearTimeout(glowTimer))
async function refreshCheck() { await mutate(() => bidApi.refreshCheck(id.value), true) }
const reopenBlock = computed(() => ['preparing', 'running'].includes(project.value?.model.state ?? ''))
// 重新检查：退回待开标后立即再检查一遍；“不是问题”保留，模型补查结果清掉
async function recheck() {
  if (!project.value || pending.value || reopenBlock.value) return
  try { await ElMessageBox.confirm('在本机重新检查一遍。标过“不是问题”的事项会保留；模型补查的结果会清掉，需要时再交模型补查。', '重新检查', { confirmButtonText: '重新检查', cancelButtonText: '取消' }) }
  catch { return }
  await mutate(() => bidApi.reopen(id.value), true)
  if (!error.value) await mutate(() => bidApi.startCheck(id.value))
}
async function reopen() {
  if (!project.value || pending.value || reopenBlock.value) return
  try { await ElMessageBox.confirm('项目将退回“待开标”，可以修改条款、增减投标文件，然后重新开始检查。标过“不是问题”的事项会保留；模型补查需要重新确认发送。', '返回修改材料', { confirmButtonText: '返回修改', cancelButtonText: '取消' }) }
  catch { return }
  await mutate(() => bidApi.reopen(id.value), true)
}
async function exportReport() {
  if (exporting.value) return
  exporting.value = true
  try { if (await reportApi.bid(id.value, 'full') === 'downloaded') ElMessage.success('问题清单已下载') }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '导出失败，请稍后重试') }
  finally { exporting.value = false }
}
async function reparse() { await mutate(() => bidApi.parse(id.value)) }
async function renameBidder(bidder: string, name: string) { await mutate(() => bidApi.renameBidder(id.value, bidder, name)); if (phase.value === 'results') void loadCheck() }
// 唱标价（V34-B-06e）：失败时在卡片内提示，不弹全局错误
const openingFailed = ref<Record<string, string>>({})
const openingLocked = computed(() => pending.value || phase.value === 'checking'
  || (phase.value === 'materials' && project.value?.busy)
  || (phase.value === 'results' && (reopenBlock.value || project.value?.files.some(f => f.original_deleted))))
async function saveOpening(bidder: string, body: BidOpeningPriceInput, done: () => void) {
  if (openingLocked.value || saving) return
  const current = generation
  const projectId = id.value
  const checked = phase.value === 'results'
  delete openingFailed.value[bidder]
  pending.value = true
  ++edits; ++checkRequest; saving++; resetTimer()
  try {
    if (checked) {
      const updated = await bidApi.openingPrice(projectId, bidder, body)
      if (current !== generation) return
      check.value = updated
      await load(false) // 抽屉的唱标价沿用项目详情里的服务端格式
    } else {
      const updated = await bidApi.openingPriceBefore(projectId, bidder, body)
      if (current !== generation) return
      accept(updated)
    }
    if (current === generation) done()
  } catch (e) {
    if (current === generation) {
      openingFailed.value[bidder] = openingError(e, body)
      void load(false)
    }
  } finally { saving--; pending.value = false }
}
async function removeBidder(bidder: string) { await mutate(() => bidApi.removeBidder(id.value, bidder)); if (!error.value) bidderOpen.value = '' }
async function addBidder() {
  if (blocked.value) return
  try {
    const { value } = await ElMessageBox.prompt('以投标函上的全称为准；之后可以把这家的文件拖到卡片上。', '添加投标人', {
      confirmButtonText: '添加', cancelButtonText: '取消', inputPlaceholder: '投标人全称',
      inputValidator: v => (!!v.trim() && v.trim().length <= 255) || '请输入投标人名称' })
    await mutate(() => bidApi.addBidder(id.value, value.trim()))
  } catch { /* 取消 */ }
}
function choose(bidder?: string, name?: string) { if (!blocked.value) { target.value = bidder; targetName.value = name; picker.value?.click() } }
async function upload(files: File[], bidder?: string, groups?: string[]) {
  if (!files.length || !project.value) return
  if (files.some(f => f.size > project.value!.limits.file_mb * 1024 * 1024)) {
    error.value = `单个文件不能超过 ${project.value.limits.file_mb} MB`
    if (drawerBidder.value) ElMessage.error(error.value)
    return
  }
  percent.value = 0
  await mutate(() => bidApi.upload(id.value, files, bidder, groups, (loaded, total) => { percent.value = Math.round(loaded / total * 100) }))
  percent.value = null
}
function picked(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''
  if (targetName.value) void upload(files, undefined, files.map(() => targetName.value!))
  else void upload(files, target.value)
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
  grouped.value = Array.from(map, ([name, files]) => ({ original: name || '（未放在文件夹中的文件）', name: matchInvitee(name, invitees.value) ?? name, files })); groupDialog.value = true
}
function pickedFolders(event: Event) {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  input.value = ''; prepareGroups(files.map(file => ({ file, path: file.webkitRelativePath || file.name })))
}
async function drop(event: DragEvent, bidder?: string, folderMode = false, name?: string) {
  overWaiting.value = null; overAdd.value = false; overTender.value = false
  if (blocked.value) return
  // 必须在首次 await 前取得全部入口，部分浏览器在事件结束后清除 dataTransfer。
  const entries = Array.from(event.dataTransfer?.items ?? []).map(item => item.webkitGetAsEntry()).filter(Boolean) as unknown as Entry[]
  const flat = Array.from(event.dataTransfer?.files ?? [])
  try {
    const all: { path: string; file: File }[] = []
    for (const entry of entries) all.push(...await walk(entry, ''))
    const files = all.length ? all : flat.map(file => ({ file, path: file.name }))
    if (folderMode) prepareGroups(files)
    else if (name) await upload(files.map(i => i.file), undefined, files.map(() => name))
    else await upload(files.map(i => i.file), bidder)
  } catch { error.value = '文件夹读取失败，请用“导入文件夹”或逐家加入文件' }
}
const groupError = ref('')
function inviteeQuery(query: string, done: (items: { value: string }[]) => void) {
  const k = nameKey(query)
  done(invitees.value.filter(n => !k || nameKey(n).includes(k)).map(value => ({ value })))
}
async function confirmGroups() {
  const names = grouped.value.map(g => g.name.trim())
  if (names.some(n => !n) || new Set(names.map(nameKey)).size !== names.length) { groupError.value = '请为每组填写不同的投标人名称'; return }
  groupError.value = ''
  const files: File[] = [], groups: string[] = []
  grouped.value.forEach((g, i) => g.files.forEach(f => { files.push(f); groups.push(names[i]!) }))
  await upload(files, undefined, groups)
  if (error.value) { groupError.value = error.value; error.value = '' }
  else { groupDialog.value = false; grouped.value = [] }
}
async function removeProject() {
  if (!project.value || pending.value) return
  try { await ElMessageBox.confirm('将删除本项目的全部原件、密码与记录，且无法恢复。', '删除初审项目', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  pending.value = true
  try { await bidApi.remove(id.value); await router.push('/bid') }
  catch (e) { error.value = e instanceof ApiError ? e.message : '删除失败' }
  finally { pending.value = false }
}
// 投标人卡：材料阶段点开抽屉；结果出来后点卡片只看这一家，再点取消
function selectBidder(bid: string) { selected.value = selected.value === bid ? '' : bid }
const drawerBidder = computed(() => project.value?.bidders.find(b => b.id === bidderOpen.value))
const drawerSigning = computed(() => (check.value?.signing ?? []).filter(s => s.bidder_id === bidderOpen.value))
</script>

<template>
  <div class="page bidp">
    <nav class="crumb" aria-label="位置">
      <router-link to="/bid">标书初审</router-link>
      <template v-if="folderName"><ChevronRight :size="14" :stroke-width="1.75" aria-hidden="true" /><router-link :to="{ path: '/bid', query: { folder: project?.folder_id } }">{{ folderName }}</router-link></template>
    </nav>
    <p v-if="loading" role="status" class="hint">正在读取项目…</p>
    <SiNotice v-if="error && !project" :title="error" type="error" :closable="false" />
    <template v-if="project">
      <header class="head">
        <h1 :title="project.title">{{ project.title }}</h1>
        <div class="acts">
          <span v-if="startBlock && phase === 'materials'" class="why">{{ startBlock }}</span>
          <el-button v-if="phase === 'materials'" type="primary" :class="{ 'start-glow': startGlow }" :disabled="!canStart" @click="startCheck"><Play :size="16" :stroke-width="1.75" />开始检查</el-button>
          <template v-else-if="phase === 'results'">
            <el-button v-if="project.policy === 'local_only'" class="ai-on" :disabled="pending" @click="enableModel"><Sparkle :size="16" :stroke-width="1.75" />开启模型补查</el-button>
            <el-button :disabled="pending || reopenBlock" @click="recheck"><RefreshCw :size="16" :stroke-width="1.75" />重新检查</el-button>
            <el-button type="primary" :loading="exporting" :disabled="!auth.can('use.export')" :title="auth.can('use.export') ? '' : '当前账号没有导出权限'" @click="exportReport"><Download :size="16" :stroke-width="1.75" />导出问题清单</el-button>
          </template>
          <RowMore :disabled="pending">
            <el-dropdown-item @click="openSettings">项目设置</el-dropdown-item>
            <el-dropdown-item v-if="phase === 'results'" :disabled="reopenBlock" @click="reopen">返回修改材料</el-dropdown-item>
            <el-dropdown-item v-else :disabled="blocked || !project.files.length" @click="reparse">重新读取全部文件</el-dropdown-item>
            <el-dropdown-item v-if="(project.sent_count ?? 0) > 0" @click="sentOpen = true">查看实际发送内容</el-dropdown-item>
            <el-dropdown-item divided class="danger-item" @click="removeProject">删除项目</el-dropdown-item>
          </RowMore>
        </div>
      </header>
      <div class="badges">
        <span class="si-badge is-line"><Calendar :size="13" :stroke-width="1.75" />开标 {{ project.opening_date }}</span>
        <span class="si-badge is-line"><Users :size="13" :stroke-width="1.75" />{{ project.bidders.length }} 家投标人</span>
        <span v-if="categoryName" class="si-badge is-line"><Tags :size="13" :stroke-width="1.75" />{{ categoryName }}</span>
        <span v-if="project.policy === 'local_only'" class="si-badge is-brand"><Lock :size="13" :stroke-width="1.75" />只在本机处理</span>
        <SiTrip v-if="trip" class="head-trip" :trip="trip" scale="bar" :since="phase === 'checking' ? project.last_activity_at : null" :seconds="tripSeconds" @act="toModelCheck" />
      </div>
      <SiNotice v-if="error" class="alert" :title="error" type="error" @close="error = ''" />
      <SiNotice v-if="notice" class="alert" :title="notice" type="warning" @close="notice = ''" />
      <SiNotice v-if="project.check.state === 'failed'" class="alert" type="error" :closable="false" :title="`检查没有完成：${project.check.error ?? '请重新开始'}`" />

      <!-- 招标文件卡：满宽；没有文件时就是拖放区 -->
      <section v-if="!tender" class="tender drop-card si-card" :class="{ over: overTender }" role="button" tabindex="0" :aria-disabled="blocked"
        @click="choose()" @keydown.enter="choose()" @dragover.prevent="overTender = !blocked" @dragleave.self="overTender = false" @drop.prevent="drop($event)">
        <span class="si-mod-icon mod"><UploadCloud :size="20" :stroke-width="1.75" /></span>
        <div class="grow"><b>先加入招标文件</b><span>拖入或点击选择 · Word、PDF、Excel 或图片 · 正文、答疑、补遗与澄清可分别加入，最多 {{ project.limits.tender_files }} 份</span></div>
        <el-button :disabled="blocked" @click.stop="choose()">选择文件</el-button>
      </section>
      <section v-else class="tender si-card" :class="{ over: overTender }" @dragover.prevent="overTender = !blocked" @dragleave.self="overTender = false" @drop.prevent="drop($event)">
        <span class="si-mod-icon mod"><BookOpen :size="20" :stroke-width="1.75" /></span>
        <div class="grow"><b>招标文件<span v-if="tender.badge" class="si-badge" :class="`is-${tender.badge.tone}`"><i class="d" />{{ tender.badge.text }}</span></b><span>{{ tender.line }}</span></div>
        <el-button v-if="project.stage === 'preparing' && tenderKey" type="primary" @click="tenderOpen = true"><ListChecks :size="16" :stroke-width="1.75" />核对条款</el-button>
        <el-button v-else text type="primary" @click="tenderOpen = true">查看条款<ChevronRight :size="16" :stroke-width="1.75" /></el-button>
      </section>

      <section class="opening-desk si-card" aria-labelledby="opening-desk-title">
        <header class="desk-head">
          <h2 id="opening-desk-title">开标桌</h2>
          <span v-if="conclusion" class="desk-help">点投标人卡片只看这一家，再点取消</span>
          <div v-if="conclusion" class="desk-summary">
            <span v-if="preliminary" class="si-badge is-info">初步</span>
            <span v-if="conclusion.severe.length" class="si-badge is-hi">严重 {{ conclusion.severe.length }}</span>
            <span v-else-if="check?.ocr_result?.state === 'final'" class="si-badge">没有严重问题</span>
            <span class="si-badge">提示 {{ conclusion.hint.length }}</span>
          </div>
        </header>
        <div class="bidders si-stagger" :class="{ lone: !project.bidders.length && !waiting.length, dim: !!selected }">
          <BidderTile v-for="b in project.bidders" :key="b.id" :bidder="b" :files="filesOf(b.id)" :busy="project.busy" :phase="phase" :disabled="blocked"
            :severe="conclusion ? counts[b.id]?.severe : undefined" :hint="conclusion ? counts[b.id]?.hint : undefined" :preliminary="preliminary" :selected="selected === b.id" :price="check?.pricing?.bidders[b.id]"
            @open="bidderOpen = b.id" @select="selectBidder(b.id)" @drop="e => drop(e, b.id)" />
          <div v-for="name in waiting" :key="name" class="bc-wait" :class="{ over: overWaiting === name }" role="button" tabindex="0" :aria-disabled="blocked" :aria-label="`为 ${name} 加入投标文件`"
            @click="choose(undefined, name)" @keydown.enter="choose(undefined, name)" @dragover.prevent="overWaiting = name" @dragleave.self="overWaiting = null" @drop.prevent.stop="drop($event, undefined, false, name)">
            <b :title="name">{{ name }}</b><span>名单中 · 还没交文件</span><span class="t4">拖入这家的文件，或点击选择</span>
          </div>
          <div v-if="!locked" class="bc-add" :class="{ over: overAdd }" role="button" tabindex="0" :aria-disabled="blocked"
            @click="addBidder" @keydown.enter="addBidder" @dragover.prevent="overAdd = true" @dragleave.self="overAdd = false" @drop.prevent.stop="drop($event, undefined, true)">
            <span class="add-ic"><component :is="project.bidders.length ? Plus : FolderOpen" :size="20" :stroke-width="1.75" /></span>
            <b>{{ project.bidders.length || waiting.length ? '添加投标人' : '把各家的投标文件夹一起拖进来' }}</b>
            <span>{{ project.bidders.length || waiting.length ? '拖入文件夹即可建卡' : '每个文件夹建一家投标人，上传前可核对名称' }}</span>
            <button type="button" class="link" :disabled="blocked" @click.stop="folders?.click()">导入文件夹</button>
          </div>
        </div>
      </section>
      <p v-if="phase === 'materials'" class="limits num">单个文件 {{ project.limits.file_mb }} MB 以内 · PDF 不超过 {{ project.limits.pdf_pages }} 页 · 全项目扫描页与图片合计 {{ project.limits.ocr_pages }} 页 · 至少 2 家才比对串标线索</p>

      <!-- 检查中：不出任何结论 -->
      <div v-if="phase === 'checking'" class="result-wait si-card">
        <ListChecks :size="24" :stroke-width="1.75" />
        <div><b>检查完成后在这里列出问题</b><p>可以离开页面；严重问题排在提示前面。</p></div>
      </div>
      <BidResults v-else-if="phase === 'results'" class="results" :project="project" :bidder="selected" :model-key="modelKey" @changed="load(false)" @refresh="refreshCheck" @clear="selected = ''" @bidder="selectBidder" />
      <footer v-if="phase === 'materials'" class="page-foot"><p class="hint">系统只指出问题并定位原件，不作否决或串通投标的认定。</p></footer>
    </template>

    <input ref="picker" type="file" class="file-input" multiple accept=".docx,.doc,.wps,.rtf,.pdf,.xlsx,.xls,.et,.jpg,.jpeg,.png" @change="picked">
    <input ref="folders" type="file" class="file-input" multiple webkitdirectory @change="pickedFolders">
    <BidSentDrawer v-if="project" v-model="sentOpen" :project-id="id" />

    <!-- 招标文件与条款：右侧抽屉 -->
    <el-drawer v-if="project" v-model="tenderOpen" size="min(1180px, 94vw)" append-to-body title="招标文件与条款" class="bid-drawer">
      <section class="d-sec">
        <div class="d-head"><h3>招标文件</h3><span class="hint num">{{ tenderFiles.length }} / {{ project.limits.tender_files }}</span></div>
        <BidFiles v-if="tenderFiles.length" :project-id="id" :files="tenderFiles" :disabled="blocked" :busy="project.busy" :row-errors="rowErrors" @remove="removeFile" @password="setPassword" />
        <div v-if="!locked && tenderFiles.length < project.limits.tender_files" class="si-drop slim" :aria-disabled="blocked" role="button" tabindex="0"
          @click="choose()" @keydown.enter="choose()" @dragover.prevent @drop.prevent="drop($event)">
          <UploadCloud :size="18" :stroke-width="1.75" /><p class="si-drop-title">{{ tenderFiles.length ? '加入答疑、补遗或澄清文件' : '拖入招标文件，或点击选择' }}</p>
        </div>
      </section>
      <BidRequirements v-if="tenderKey" :project-id="id" :policy="project.policy" :disabled="blocked" :tender-key="tenderKey" @changed="load(false)" @confirmed="termsConfirmed" />
      <p v-else class="hint d-wait">{{ project.busy ? '正在读取招标文件…读取完后自动列出 ★、否决与“必须 / 不得”条款。' : '加入招标文件后，系统自动列出 ★、否决与“必须 / 不得”条款，由你核对确认。' }}</p>
    </el-drawer>

    <!-- 一家投标人：文件明细、密码、加文件、改名、删除；结果出来后另有签章页 -->
    <el-drawer v-if="project" :model-value="!!drawerBidder" size="480px" append-to-body :title="drawerBidder ? `${drawerBidder.code.replace(/[（）()]/g, '')}　${drawerBidder.short || drawerBidder.name}` : ''" class="bid-drawer" @update:model-value="(v: boolean) => { if (!v) bidderOpen = '' }">
      <template v-if="drawerBidder">
        <BidderCard :key="drawerBidder.id" flat :project-id="id" :bidder="drawerBidder" :files="filesOf(drawerBidder.id)" :disabled="blocked" :busy="project.busy" :row-errors="rowErrors" :suggest="suggestions[drawerBidder.id]"
          :rate-quote="project.quote_mode === 'rate' || project.quote_mode === 'discount'" :opening-failed="openingFailed[drawerBidder.id]" :opening-disabled="openingLocked"
          @rename="name => renameBidder(drawerBidder!.id, name)" @remove="removeBidder(drawerBidder!.id)" @pick="choose(drawerBidder!.id)" @drop="e => drop(e, drawerBidder!.id)"
          @password="setPassword" @remove-file="removeFile" @opening="(body, done) => saveOpening(drawerBidder!.id, body, done)" @adopt="name => renameBidder(drawerBidder!.id, name)" />
        <p v-if="locked" class="hint d-lock">开始检查后材料锁定；要增减文件，在页头“更多”里选“返回修改材料”。</p>
        <section v-if="phase === 'results' && check" class="d-sec sign">
          <div class="d-head"><h3>签章页</h3><span class="hint num">已标 {{ drawerSigning.filter(s => s.mark).length }} / {{ drawerSigning.length }}</span></div>
          <BidSignSheets v-if="drawerSigning.length" :pages="drawerSigning" />
          <p v-else class="hint">没有在这一家的文件中找到投标函、授权委托书、报价表、承诺函等要求盖章签字的格式页；扫描页识别完成后会补上。</p>
        </section>
      </template>
    </el-drawer>

    <el-dialog :model-value="!!page" width="min(920px, 92vw)" append-to-body :title="page ? `${fileNames[page.fileId] ?? '原件'} · 第 ${page.page} 页` : ''" @update:model-value="(v: boolean) => { if (!v) page = null }">
      <div v-if="page" class="page-view">
        <img :src="bidApi.pageUrl(id, page.fileId, page.page, 1100, page.page === page.first ? page.loc : null)" :alt="`第 ${page.page} 页`">
      </div>
      <template #footer>
        <div v-if="page" class="pager">
          <el-button :disabled="page.page <= 1" @click="page.page--"><ChevronLeft :size="16" :stroke-width="1.75" />上一页</el-button>
          <span class="num">{{ page.page }} / {{ pageCount }}</span>
          <el-button :disabled="page.page >= pageCount" @click="page.page++">下一页<ChevronRight :size="16" :stroke-width="1.75" /></el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="groupDialog" title="核对投标人分组" width="600px" :close-on-click-modal="!pending" append-to-body>
      <p class="dialog-lede">每个文件夹对应一家投标人。请把名称改成投标函上的全称；同名的已有投标人会把文件并入该卡片。<template v-if="invitees.length">能对上受邀名单的已预填全称。</template></p>
      <ul class="group-list">
        <li v-for="group in grouped" :key="group.original">
          <span class="folder"><FolderOpen :size="14" :stroke-width="1.75" aria-hidden="true" /><span :title="group.original">{{ group.original }}</span></span>
          <el-autocomplete v-if="invitees.length" v-model="group.name" :fetch-suggestions="inviteeQuery" maxlength="255" placeholder="投标人名称，可从名单中选" :aria-label="`${group.original} 的投标人名称`" :disabled="pending" />
          <el-input v-else v-model="group.name" maxlength="255" placeholder="投标人名称" :aria-label="`${group.original} 的投标人名称`" :disabled="pending" />
          <span class="hint num">{{ group.files.length }} 份</span>
        </li>
      </ul>
      <p v-if="groupError" class="error" role="alert">{{ groupError }}</p>
      <template #footer><el-button :disabled="pending" @click="groupDialog = false">取消</el-button><el-button type="primary" :loading="pending" @click="confirmGroups">{{ percent !== null ? `正在上传 ${percent}%` : '按分组上传' }}</el-button></template>
    </el-dialog>
    <BidSettingsDialog v-if="project" v-model="settingsDialog" title="项目设置" confirm-text="保存" :initial="{ title: project.title, opening_date: project.opening_date, policy: project.policy, category_id: project.category_id, folder_id: project.folder_id }" :locked="locked" :busy="pending" :error="settingsError" @submit="saveSettings" />
  </div>
</template>

<style scoped>
.ai-on { color: var(--si-ai-ink); }
.ai-on svg { color: var(--si-ai); }
/* 条款确认后“开始检查”的光晕：向外扩散三次；特效关闭时只显示静态描边 */
.start-glow { animation: start-glow 1.2s var(--si-ease-out) 3; }
@keyframes start-glow { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--si-brand) 50%, transparent); } 100% { box-shadow: 0 0 0 14px color-mix(in srgb, var(--si-brand) 0%, transparent); } }
:global(html[data-fx="off"]) .start-glow { animation: none; box-shadow: var(--si-ring); }
/* 工作区：内容宽 1200，1440 宽时投标人每行 4 张 */
.bidp { max-width: 1280px; display: flex; flex-direction: column; min-height: 100%; box-sizing: border-box; }
.crumb { display: flex; align-items: center; gap: 4px; font: var(--si-font-meta); color: var(--si-ink-3); }
.crumb a { color: var(--si-ink-3); text-decoration: none; }
.crumb a:hover { color: var(--si-blue-ink); }
.crumb svg { color: var(--si-ink-5); }
.head { display: flex; align-items: center; gap: 16px; margin-top: 8px; }
h1 { flex: 1; min-width: 0; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-title); color: var(--si-ink-1); }
.acts { flex: none; display: flex; align-items: center; gap: 8px; }
.acts .el-button :deep(> span) { gap: 6px; }
.why { font: var(--si-font-meta); color: var(--si-ink-3); }
.badges { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 10px; }
.head-trip { margin-left: auto; }
.alert { margin-top: 16px; }

.tender { display: flex; align-items: center; gap: 14px; margin-top: 24px; padding: 14px 20px; }
.tender.over { border-color: var(--si-brand-2); background: var(--si-blue-tint); }
.mod { --mc: var(--si-m-bid); --mc-soft: var(--si-m-bid-soft); width: 40px; height: 40px; }
.tender .grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.tender b { display: flex; align-items: center; gap: 8px; font: var(--si-font-h3); color: var(--si-ink-1); }
.tender .grow > span { font: var(--si-font-meta); color: var(--si-ink-3); }
.tender .el-button :deep(> span) { gap: 4px; }
.drop-card { border: 1.5px dashed var(--si-hair-strong); box-shadow: none; cursor: pointer; }
.drop-card:hover { border-color: var(--si-m-bid); }
.drop-card[aria-disabled="true"] { cursor: progress; opacity: .7; }

.opening-desk { margin-top: 20px; padding: 18px 20px 14px; background: linear-gradient(180deg, var(--si-surface-1), color-mix(in srgb, var(--si-m-bid-soft) 55%, var(--si-surface-1))); }
.desk-head, .desk-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.desk-head { gap: 8px 12px; margin-bottom: 16px; }
.desk-head h2 { margin: 0; font: var(--si-font-h2); color: var(--si-ink-1); }
.desk-help { color: var(--si-ink-3); font: var(--si-font-meta); }
.desk-summary { margin-left: auto; }
.bidders { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); align-items: end; gap: 18px 14px; padding: 10px 0 12px; border-bottom: 3px solid color-mix(in srgb, var(--si-m-bid) 36%, var(--si-hair)); }
/* 只看一家时，其余几份退后 */
.bidders.dim > :deep(.bc:not(.on)) { opacity: .55; }
@media (max-width: 1280px) { .bidders { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@media (max-width: 1120px) { .bidders { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 840px) { .bidders { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.bc-add, .bc-wait { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-height: 112px; padding: 14px 16px; box-sizing: border-box; border: 1.5px dashed var(--si-hair-strong); border-radius: var(--si-radius-card); text-align: center; color: var(--si-ink-3); font: var(--si-font-caption); cursor: pointer; transition: border-color var(--si-d2) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out); }
.bc-add:hover, .bc-wait:hover { border-color: var(--si-m-bid); }
.bc-add.over, .bc-wait.over { border-style: solid; border-color: var(--si-m-bid); background: var(--si-m-bid-soft); }
.bc-add b, .bc-wait b { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-label); color: var(--si-ink-2); }
.bc-wait { align-items: flex-start; text-align: left; }
.t4 { color: var(--si-ink-4); }
.add-ic { display: grid; place-items: center; width: 36px; height: 36px; margin-bottom: 2px; border-radius: var(--si-radius-control); background: var(--si-m-bid-soft); color: var(--si-m-bid); }
.bidders.lone .bc-add { grid-column: 1 / -1; min-height: 180px; }
.bidders.lone .bc-add b { font: var(--si-font-h3); }
.link { margin-top: 4px; padding: 0; border: 0; background: none; font: var(--si-font-caption); color: var(--si-blue-ink); cursor: pointer; }
.link:hover { text-decoration: underline; }
[aria-disabled="true"] { cursor: progress; }
.limits { margin: 12px 0 0; font: var(--si-font-caption); color: var(--si-ink-4); }

.result-wait { display: flex; align-items: center; gap: 16px; margin-top: 24px; padding: 24px; border-style: dashed; background: transparent; box-shadow: none; color: var(--si-ink-4); }
.result-wait b { font: var(--si-font-h3); color: var(--si-ink-1); }
.result-wait p { margin: 2px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.results { margin-top: 24px; }
.page-foot { margin-top: auto; padding-top: 48px; }
.page-foot .hint { margin: 0; padding-top: 14px; border-top: 1px solid var(--si-hair); }

.d-sec { margin-bottom: 24px; }
.d-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.d-head h3 { margin: 0; font: var(--si-font-h3); color: var(--si-ink-1); }
.d-sec .si-drop.slim { flex-direction: row; justify-content: center; gap: 10px; margin-top: 12px; padding: 14px 16px; }
.d-sec .si-drop.slim .si-drop-title { margin: 0; font: var(--si-font-label); color: var(--si-ink-2); }
.d-wait { margin: 0; }
.d-lock { margin: 16px 0 0; }
.sign { margin-top: 28px; }
.file-input { display: none; }
.page-view { max-height: 70vh; overflow: auto; background: var(--si-stage); border-radius: var(--si-radius-card); }
.page-view img { display: block; width: 100%; }
.pager { display: flex; align-items: center; justify-content: center; gap: 16px; }
.pager .el-button svg { margin: 0 4px; }
.dialog-lede { margin: 0 0 16px; font: var(--si-font-body); color: var(--si-ink-2); }
.group-list { margin: 0; padding: 0; list-style: none; }
.group-list .el-autocomplete { width: 100%; }
.group-list li { display: grid; grid-template-columns: 160px minmax(0, 1fr) 48px; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--si-hair-faint); }
.folder { display: flex; align-items: center; gap: 6px; min-width: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.folder span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.group-list .hint { text-align: right; }
.error { margin: 12px 0 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
