<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { FileUp, ShieldCheck } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { assistantApi, confirmationTask, type Session, type SessionFile } from '@/api/assistant'
import { modelsApi, type AvailableModel } from '@/api/models'
import { RUN_ERROR_MESSAGES, runsApi, type RunDetail, type RunListItem } from '@/api/runs'
import { MAX_NOTE_LENGTH, type Source, type Stance } from '@/api/tasks'
import EmptyState from '@/components/EmptyState.vue'
import LocalCheckList from '@/components/result/LocalCheckList.vue'
import ReviewCeremony from '@/components/result/ReviewCeremony.vue'
import RequirementsDrawer from '@/components/RequirementsDrawer.vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import RunResult from '@/components/workspace/RunResult.vue'
import { useAuthStore } from '@/stores/auth'
import { SOURCE_OPTIONS, STANCE_OPTIONS, toOptions } from '@/utils/labels'

/**
 * 工作台“全面审核”视图（V32-B-01 2）：设置 → 审核中 → 结果，三种状态按会话数据决定。
 * 文件只上传一次、只确认一次；这里的“确认发送”只列本轮新增发送的内容（补充说明、主体说明），已确认文件不再逐个核对。
 */
const props = defineProps<{ session: Session; fresh?: boolean }>()
const emit = defineEmits<{ refresh: []; confirm: []; add: []; pick: []; discuss: [anchor: { id: string; no: string; title: string; runNo: number }] }>()
const auth = useAuthStore()

// ---- 轮次 ----
const rounds = ref<RunListItem[]>([])
const roundsLoaded = ref(false)
const selectedRun = ref('')
const composing = ref(false)
const freshRun = ref(false)
let version = 0
async function loadRounds(select?: 'latest') {
  const current = ++version
  try {
    const list = (await assistantApi.runs(props.session.id)).items
    if (current !== version) return
    rounds.value = list
    // 列表按轮次倒序或正序都不假设：取 run_no 最大者为最新
    const latest = [...list].sort((a, b) => b.run_no - a.run_no)[0]?.id ?? ''
    if (select === 'latest' || !list.some(r => r.id === selectedRun.value)) selectedRun.value = latest
  } catch { /* 读不到时保持设置视图，用户可刷新 */ }
  finally { if (current === version) roundsLoaded.value = true }
}
watch(() => props.session.id, () => { rounds.value = []; roundsLoaded.value = false; selectedRun.value = ''; composing.value = false; void loadRounds() }, { immediate: true })

// 审核结束（review_running 由真变假）：取新轮次并显示结果
watch(() => props.session.review_running, async (running, was) => {
  if (was && !running) { running_.value = null; composing.value = false; freshRun.value = true; await loadRounds('latest') }
  else if (running) { composing.value = false; void pollRun() }
})

// ---- 审核中：轮询最新轮次进度（会话详情由页面轮询）----
const running_ = ref<RunDetail | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
async function pollRun() {
  clearTimeout(timer)
  if (!props.session.review_running) return
  try {
    const list = (await assistantApi.runs(props.session.id)).items
    const latest = [...list].sort((a, b) => b.run_no - a.run_no)[0]
    if (latest) running_.value = await runsApi.get(latest.id)
  } catch { /* 网络中断时沿用上次进度 */ }
  if (props.session.review_running) timer = setTimeout(pollRun, 2000)
}
onBeforeUnmount(() => clearTimeout(timer))
if (props.session.review_running) void pollRun()
const ceremonyTask = computed(() => ({ ...confirmationTask(props.session), status: 'reviewing' as const,
  latest_run: running_.value ? { id: running_.value.id, run_no: running_.value.run_no, status: running_.value.status } : null,
  queue_position: running_.value?.queue_position ?? null }))

// ---- 设置 ----
const files = computed(() => props.session.files.filter(f => !f.media))
const confirmedFiles = computed(() => files.value.filter(f => !!f.confirmed_at))
const pendingFiles = computed(() => files.value.filter(f => !f.confirmed_at))
const picked = ref<string[]>([])
const mainId = ref('')
const known = new Set<string>()
watch(() => confirmedFiles.value.map(f => f.id).join(','), () => {
  const ids = confirmedFiles.value.map(f => f.id)
  // 新确认的文件默认选中；已不存在的去掉
  picked.value = [...picked.value.filter(id => ids.includes(id)), ...ids.filter(id => !picked.value.includes(id) && !known.has(id))]
  ids.forEach(id => known.add(id))
  if (!picked.value.includes(mainId.value)) mainId.value = confirmedFiles.value.find(f => picked.value.includes(f.id) && f.is_main)?.id ?? picked.value[0] ?? ''
}, { immediate: true })
watch(picked, ids => { if (!ids.includes(mainId.value)) mainId.value = ids[0] ?? '' })

// 会话的立场、来源只是建会话时的占位值；默认与原审核页一致（销售、我方发出），再被上次选择覆盖
const stance = ref<Stance>('sales')
const source = ref<Source>('outgoing')
const note = ref('')
const noteOpen = ref(false)
const reqDrawer = ref(false)
const stanceOptions = toOptions(STANCE_OPTIONS)
const sourceOptions = toOptions(SOURCE_OPTIONS)
// 立场、来源沿用该用户上次审核时的选择（与原审核页同一份本地记录）
const prefKey = () => `aireview:last-choice:${auth.user?.id ?? ''}`
try {
  const saved = JSON.parse(localStorage.getItem(prefKey()) ?? 'null') as { stance?: string; source?: string } | null
  if (saved?.stance && saved.stance in STANCE_OPTIONS) stance.value = saved.stance as Stance
  if (saved?.source && saved.source in SOURCE_OPTIONS) source.value = saved.source as Source
} catch { /* 浏览器禁用本地存储时用默认值 */ }
function savePrefs() { try { localStorage.setItem(prefKey(), JSON.stringify({ stance: stance.value, source: source.value })) } catch { /* 忽略 */ } }

const models = ref<AvailableModel[]>([])
const modelId = ref<number>()
const modelError = ref('')
const modelsLoading = ref(false)
async function loadModels() {
  if (props.session.policy === 'local_only' || models.value.length) return
  modelsLoading.value = true; modelError.value = ''
  try {
    models.value = (await modelsApi.list('review')).items
    modelId.value = models.value.find(m => m.id === modelId.value)?.id ?? models.value.find(m => m.is_default)?.id ?? models.value[0]?.id
    if (!modelId.value) modelError.value = '没有可用的审核模型，请联系管理员'
  } catch (e) { modelError.value = e instanceof ApiError ? e.message : '模型列表加载失败，请刷新页面' }
  finally { modelsLoading.value = false }
}

// 本轮新增发送的内容：主体说明来自服务端按当前材料生成的外发正文，只取这一行，不展示全文
const subjects = ref<string[]>([])
const previewVersion = ref<number | null>(null)
const previewError = ref('')
async function loadPreview() {
  previewError.value = ''
  try {
    const data = await assistantApi.outboundReview(props.session.id)
    previewVersion.value = data.material_version
    const line = /【主体说明】([^\n]*)/.exec(data.text)?.[1] ?? ''
    subjects.value = line && line !== '无' ? line.split('；').filter(Boolean) : []
  } catch (e) { previewError.value = e instanceof ApiError ? e.message : '读取本轮发送内容失败，请刷新页面' }
}

const stage = computed<'loading' | 'running' | 'result' | 'local' | 'setup'>(() => {
  if (props.session.review_running) return 'running'
  if (props.session.policy === 'local_only') return 'local'
  if (!roundsLoaded.value) return 'loading'
  return rounds.value.length && !composing.value ? 'result' : 'setup'
})
const canConfigure = computed(() => stage.value === 'setup' && ['active', 'awaiting_confirm'].includes(props.session.status))
watch([stage, () => props.session.material_version, () => confirmedFiles.value.length], ([s]) => {
  if (s === 'setup' && confirmedFiles.value.length) { void loadModels(); void loadPreview() }
}, { immediate: true })

const blocker = computed(() => {
  if (props.session.original_deleted) return '原件已按保留期限删除，不能再审核'
  if (props.session.generating) return '回复生成中，完成后再发起审核'
  if (props.session.status === 'parsing') return '文件正在解析，请稍候'
  if (!confirmedFiles.value.length) return '请先确认脱敏'
  if (!picked.value.length) return '请至少选择一个文件'
  if (!mainId.value) return '请选择主文件'
  if (modelsLoading.value) return '正在加载模型列表'
  if (modelError.value) return modelError.value
  if (!modelId.value) return '请选择审核模型'
  if (previewVersion.value === null) return previewError.value || '正在读取本轮发送内容'
  return ''
})
const submitting = ref(false)
const formError = ref('')
async function start() {
  if (blocker.value || submitting.value) return
  submitting.value = true; formError.value = ''
  try {
    const trimmed = note.value.trim()
    await assistantApi.createRun(props.session.id, {
      stance: stance.value, source: source.value, model_id: modelId.value, user_note: trimmed || null,
      file_ids: picked.value, main_file_id: mainId.value, expected_material_version: previewVersion.value ?? undefined,
    })
    savePrefs()
    emit('refresh')
  } catch (e) {
    const code = e instanceof ApiError ? e.code : ''
    formError.value = e instanceof ApiError ? RUN_ERROR_MESSAGES[code] ?? e.message : '发起审核失败，请重试'
    if (code === 'MATERIAL_CHANGED') { await loadPreview(); formError.value = '材料有变化，已更新下方“本轮发送的内容”，请核对后再确认' }
    if (['MATERIAL_CHANGED', 'SESSION_BUSY', 'TASK_STATE_CONFLICT', 'FILES_PARSING'].includes(code)) emit('refresh')
    if (code === 'OUR_PARTY_REQUIRED') ElMessage.warning('请在“材料”中确认哪一方是我方')
  } finally { submitting.value = false }
}
function badge(file: SessionFile) { return file.ext.toUpperCase().slice(0, 4) }
function newRound() { composing.value = true; note.value = ''; noteOpen.value = false; formError.value = '' }
function backToResult() { composing.value = false }
const loadLocal = () => assistantApi.localFindings(props.session.id)
</script>

<template>
  <div class="review-pane">
    <div v-if="stage === 'loading'" class="center"><el-skeleton :rows="4" animated /></div>

    <!-- 审核中 -->
    <section v-else-if="stage === 'running'" class="panel running">
      <ReviewCeremony :task="ceremonyTask" :run="running_" />
    </section>

    <!-- 不外发的会话：只有本地校验 -->
    <section v-else-if="stage === 'local'" class="local">
      <el-alert type="info" :closable="false" show-icon title="该会话的外发策略为“不外发，仅本地校验”：文件不离开本系统，只做金额、日期等本地校验，不产生 AI 审核意见。" />
      <LocalCheckList :loader="loadLocal" :load-key="session.id" readonly-locations />
    </section>

    <!-- 结果 -->
    <RunResult v-else-if="stage === 'result'" :session="session" :rounds="rounds" :selected-run="selectedRun" :fresh="freshRun"
      @select-run="selectedRun = $event; freshRun = false" @new-round="newRound" @discuss="emit('discuss', $event)" />

    <!-- 设置 -->
    <section v-else class="setup">
      <div class="setup-inner">
        <EmptyState v-if="!files.length" :icon="FileUp" title="先加入要审核的材料" description="文件只需上传一次，在本机解析、确认脱敏后，既可以直接提问，也可以发起全面审核。">
          <el-button type="primary" @click="emit('pick')">加入文件</el-button>
        </EmptyState>
        <template v-else>
          <el-alert v-if="session.status === 'parsing'" type="info" :closable="false" show-icon title="文件正在本机解析，完成后即可确认脱敏" />
          <el-alert v-else-if="!confirmedFiles.length" type="warning" :closable="false" show-icon title="还没有已确认脱敏的文件">
            <template #default><el-button type="primary" size="small" @click="emit('confirm')">确认脱敏（{{ pendingFiles.length }} 个文件）</el-button></template>
          </el-alert>
          <el-alert v-else-if="pendingFiles.length" type="warning" :closable="false" show-icon :title="`另有 ${pendingFiles.length} 个文件尚未确认脱敏，不会参与本轮审核`">
            <template #default><el-button size="small" @click="emit('confirm')">去确认</el-button></template>
          </el-alert>
          <div v-if="rounds.length" class="back"><el-button link type="primary" @click="backToResult">← 返回第 {{ [...rounds].sort((a, b) => b.run_no - a.run_no)[0]?.run_no }} 轮结果</el-button></div>

          <div class="grid">
            <div class="col">
              <h2>本轮审核的文件</h2>
              <el-checkbox-group v-model="picked" class="file-list">
                <label v-for="f in confirmedFiles" :key="f.id" class="file-row" :class="{ 'is-main': f.id === mainId }">
                  <el-checkbox :value="f.id" :aria-label="`审核 ${f.original_name}`" />
                  <span class="file-badge">{{ badge(f) }}</span>
                  <span class="file-name" :title="f.original_name">{{ f.original_name }}</span>
                  <em v-if="f.id === mainId" class="main-tag">主文件</em>
                  <el-button v-else-if="picked.includes(f.id)" link type="primary" @click.prevent="mainId = f.id">设为主文件</el-button>
                </label>
              </el-checkbox-group>
              <p v-if="!confirmedFiles.length" class="hint">确认脱敏后，文件会出现在这里。</p>
            </div>
            <div class="col">
              <h2>审核设置</h2>
              <div class="setting"><span class="setting-label">业务立场</span><SiSegmented v-model="stance" :options="stanceOptions" ariaLabel="业务立场" /></div>
              <div class="setting"><span class="setting-label">文件来源</span><SiSegmented v-model="source" :options="sourceOptions" ariaLabel="文件来源" /></div>
              <div class="setting">
                <span class="setting-label">审核模型</span>
                <span v-if="models.length === 1">{{ models[0]!.display_name }}</span>
                <el-select v-else v-model="modelId" placeholder="选择审核模型" aria-label="审核模型" :loading="modelsLoading" :disabled="!models.length">
                  <el-option v-for="m in models" :key="m.id" :value="m.id" :label="m.display_name" />
                </el-select>
                <p v-if="modelError" class="hint">{{ modelError }}</p>
              </div>
              <div class="setting">
                <el-input v-if="noteOpen" v-model="note" type="textarea" aria-label="本次补充说明" :rows="3" :maxlength="MAX_NOTE_LENGTH" show-word-limit placeholder="如：这是给土耳其客户的报价，请重点看付款条款" />
                <div class="links"><el-button v-if="!noteOpen" link type="primary" @click="noteOpen = true">＋ 添加补充说明</el-button><el-button link type="primary" @click="reqDrawer = true">查看审核要求</el-button></div>
              </div>
            </div>
          </div>

          <div class="sent" aria-label="本轮发送的内容">
            <div class="sent-head"><ShieldCheck :size="16" :stroke-width="1.75" /><h2>本轮发送的内容</h2></div>
            <p class="hint">已确认的 {{ picked.length }} 个文件按已核对的脱敏结果发送，不再逐个核对；本轮新增发送的只有：</p>
            <ul>
              <li><strong>主体说明</strong>：<template v-if="subjects.length"><span v-for="s in subjects" :key="s" class="subject">{{ s }}</span></template><template v-else>无</template></li>
              <li><strong>补充说明</strong>：<template v-if="note.trim()">{{ note.trim().length }} 字，发送前在本机脱敏</template><template v-else>未填写</template></li>
            </ul>
            <p class="hint">名称、金额、账号等已替换为代号；模型回复在本机还原。立场与来源以上方所选为准。</p>
          </div>
          <el-alert v-if="formError" type="error" show-icon :closable="false" :title="formError" />
          <div class="foot">
            <span v-if="blocker" class="hint">{{ blocker }}</span>
            <el-button type="primary" :loading="submitting" :disabled="!!blocker || !canConfigure" @click="start">确认发送并开始审核</el-button>
          </div>
        </template>
      </div>
    </section>
    <RequirementsDrawer v-model="reqDrawer" :stance="stance" :source="source" :user-note="note" />
  </div>
</template>

<style scoped>
.review-pane { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.center { padding: 32px; }
.running { flex: 1; min-height: 0; overflow: hidden; margin: 0 20px 16px; }
.local { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.setup { flex: 1; min-height: 0; overflow-y: auto; padding: 20px; }
.setup-inner { display: flex; flex-direction: column; gap: 16px; max-width: 920px; margin: 0 auto; }
.back { margin-bottom: -4px; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 24px; }
.col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
h2 { margin: 0; font: var(--si-font-h4); }
.file-list { display: flex; flex-direction: column; gap: 8px; }
.file-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; cursor: pointer; }
.file-row.is-main { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.file-row :deep(.el-checkbox) { height: auto; }
.file-badge { flex: none; display: grid; place-items: center; min-width: 36px; height: 28px; padding: 0 6px; border-radius: 6px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); }
.file-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); }
.main-tag { padding: 0 6px; border-radius: 4px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font: var(--si-font-label-sm); font-style: normal; }
.setting { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.setting :deep(.el-select), .setting :deep(.el-textarea) { width: 100%; }
.setting-label { color: var(--si-ink-3); font: var(--si-font-label-sm); }
.links { display: flex; gap: 16px; }
.sent { padding: 14px 16px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); }
.sent-head { display: flex; align-items: center; gap: 8px; color: var(--si-blue-ink); }
.sent p { margin: 6px 0; }
.sent ul { margin: 6px 0; padding-left: 18px; }
.sent li { margin: 4px 0; overflow-wrap: anywhere; }
.subject { display: inline-block; margin-right: 8px; padding: 0 6px; border-radius: 4px; background: var(--si-surface-2); font: var(--si-font-caption); }
.foot { display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
</style>
