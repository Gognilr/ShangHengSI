<script setup lang="ts">
// 逐字稿校对（V31-U5 第四节、V31-U5F-05 第 5.2 节）：同一发言人间隔不足 30 秒的句子合并成段显示；
// 悬停出现编辑提示与时间点，点击文字修改、点别处自动保存；改字与改发言人乐观更新，后台按顺序保存，不整页重算。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Flag, Pencil, Play } from '@lucide/vue'
import { assistantApi, type Session, type SessionFile, type Suggestion } from '@/api/assistant'
import type { PreviewNode } from '@/api/preview'
import { ApiError } from '@/api/http'
import { saveBlob } from '@/utils/save'
import { useHotkeys } from '@/utils/hotkeys'
import { groupSentences, stamp } from '@/utils/transcript'
import SiIsland from '@/components/ui/SiIsland.vue'
const props = defineProps<{ session: Session; file: SessionFile }>()
const emit = defineEmits<{ refresh: []; confirm: [] }>()
const nodes = ref<PreviewNode[]>([]), suggestions = ref<Suggestion[]>([]), error = ref(''), loading = ref(false), busy = ref(false)
const names = ref<string[]>([...props.session.participants]), addedName = ref(''), speakers = ref<Record<string,string | null>>({ ...props.file.speakers })
const readonly = computed(() => !!props.file.confirmed_at || props.session.original_deleted)
const speakerIds = computed(() => [...new Set([...Object.keys(speakers.value), ...nodes.value.map(n => String(n.speaker))])].filter(k => /^\d+$/.test(k)).sort((a,b) => Number(a)-Number(b)))
const paras = computed(() => groupSentences(nodes.value))
const audio = ref<HTMLAudioElement>(), paper = ref<HTMLElement>(), playing = ref(false), current = ref(0), duration = ref(props.file.media?.duration_ms ?? 0), speed = ref(1), following = ref(true), audioLoading = ref(false), activeLoc = ref('')
const speakerEditLoc = ref('')
const editLoc = ref(''), editText = ref(''), flash = ref('')
// 乐观保存：改动先显示，后台按顺序保存；rev 用于通知被 v-memo 缓存的段落重绘
const rev = ref(0), pendingSaves = ref(0), saveState = ref<'idle' | 'saving' | 'saved'>('idle'), notice = ref('')
let loadVersion = 0, chain: Promise<unknown> = Promise.resolve(), failed = false
let url = '', alive = true, loadingAudio: Promise<void> | null = null, timer: ReturnType<typeof setTimeout> | undefined, savedTimer: ReturnType<typeof setTimeout> | undefined
function message(e: unknown) { return e instanceof ApiError ? e.message : '读取或保存失败，请重试' }
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const [result, hints, detail] = await Promise.all([assistantApi.nodes(props.session.id, props.file.id), readonly.value ? Promise.resolve([]) : assistantApi.suggestions(props.session.id, props.file.id), assistantApi.get(props.session.id)])
    if (!alive || version !== loadVersion) return
    nodes.value = result.items.filter(n => n.kind === 'utterance'); rev.value++
    suggestions.value = hints; names.value = detail.participants
    speakers.value = { ...detail.files.find(f => f.id === props.file.id)?.speakers }
  } catch (e) { if (alive && version === loadVersion) error.value = message(e) }
  finally { if (alive && version === loadVersion) loading.value = false }
}
// 重操作（改名单、发言人姓名、采纳建议）会重算脱敏，保留整页等待；先等后台保存队列清空
async function mutate(action: () => Promise<unknown>) {
  if (readonly.value || busy.value) return
  busy.value = true; error.value = ''
  try { await chain; await action(); if (alive) { await load(); emit('refresh') } }
  catch (e) { if (alive) { error.value = message(e); await load() } }
  finally { if (alive) busy.value = false }
}
// 改字、改发言人：先改界面，后台保存；全部保存完才刷新同音建议；任何一次失败就整体回读服务器内容
function save(job: () => Promise<unknown>) {
  if (readonly.value) return
  pendingSaves.value++; saveState.value = 'saving'; error.value = ''; clearTimeout(savedTimer)
  chain = chain.then(async () => {
    try { await job() } catch (e) { failed = true; if (alive) error.value = `保存失败：${message(e)}，已恢复为服务器上的内容` }
    pendingSaves.value--
    if (pendingSaves.value > 0 || !alive) return
    const ok = !failed; failed = false
    if (ok) { saveState.value = 'saved'; savedTimer = setTimeout(() => { saveState.value = 'idle' }, 2500) } else saveState.value = 'idle'
    await load(); if (ok) emit('refresh')
  })
}
function startEdit(node: PreviewNode) { if (readonly.value || busy.value) return; editLoc.value = node.loc; editText.value = node.text ?? ''; void nextTick(() => paper.value?.querySelector<HTMLTextAreaElement>('textarea')?.focus()) }
function commitText(node: PreviewNode) {
  if (editLoc.value !== node.loc) return  // 已被 Esc 取消
  const text = editText.value
  editLoc.value = ''
  if (text === (node.text ?? '')) return
  if (!text.trim()) { notice.value = '句子不能为空，已恢复原文'; setTimeout(() => { notice.value = '' }, 3000); return }
  node.text = text; node.low_conf_spans = []; rev.value++
  save(() => assistantApi.utterance(props.session.id, props.file.id, node.loc, { text }))
}
function textKey(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key === 'Escape') { event.preventDefault(); editLoc.value = '' }
  else if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); (event.target as HTMLElement).blur() }
}
async function openSpeaker(loc: string) { speakerEditLoc.value = loc; await nextTick(); paper.value?.querySelector<HTMLElement>('.meta .el-select')?.click() }
function changeSpeaker(para: PreviewNode[], speaker: number) {
  speakerEditLoc.value = ''
  if (para[0]!.speaker === speaker) return
  const locs = para.map(n => n.loc)
  para.forEach(n => { n.speaker = speaker }); rev.value++
  for (let i = 0; i < locs.length; i += 500) { const part = locs.slice(i, i + 500); save(() => assistantApi.utterancesSpeaker(props.session.id, props.file.id, part, speaker)) }
}
function saveSpeaker(id: string) { return mutate(() => assistantApi.speakers(props.session.id, props.file.id, { ...speakers.value, [id]: speakers.value[id]?.trim() || null })) }
function setNames(values: string[]) { return mutate(() => assistantApi.participants(props.session.id, values)) }
function addName() {
  const name = addedName.value.trim()
  if (name.length < 2 || name.length > 40 || names.value.length >= 30) { error.value = '姓名须为 2～40 字，最多 30 个'; return }
  addedName.value = ''; if (!names.value.includes(name)) void setNames([...names.value, name])
}
function apply(items: Suggestion[]) { return mutate(() => assistantApi.applySuggestions(props.session.id, props.file.id, items.map(({ loc,start,end,to }) => ({loc,start,end,to})))) }
function locate(loc: string) { paper.value?.querySelector<HTMLElement>(`[data-loc="${CSS.escape(loc)}"]`)?.scrollIntoView({ block: 'nearest' }) }
function scrollbarPointer(event: PointerEvent) {
  const el = paper.value
  if (el && el.scrollHeight > el.clientHeight && event.clientX >= el.getBoundingClientRect().right - 16) following.value = false
}
function highlight(loc: string) { locate(loc); flash.value = loc; rev.value++; clearTimeout(timer); timer = setTimeout(() => { flash.value = ''; rev.value++ }, 1200) }
function spans(node: PreviewNode) {
  const chars = Array.from(node.text ?? ''), out: { text: string; low: boolean }[] = []
  chars.forEach((c,i) => { const low = (node.low_conf_spans ?? []).some(([a,b]) => i >= a! && i < b!); const previous = out[out.length-1]; if (previous?.low === low) previous.text += c; else out.push({ text:c, low }) })
  return out
}
// 录音时的打点：显示在它所处的那一句之前
const markMap = computed(() => {
  const map = new Map<string, number[]>(), list = nodes.value
  for (const m of props.file.media?.marks ?? []) {
    let hit = list[0]
    for (const n of list) { if ((n.t_start_ms ?? 0) <= m) hit = n; else break }
    if (hit) map.set(hit.loc, [...(map.get(hit.loc) ?? []), m])
  }
  return map
})
const activeIn = (para: PreviewNode[]) => para.some(n => n.loc === activeLoc.value) ? activeLoc.value : ''
const flashIn = (para: PreviewNode[]) => para.some(n => n.loc === flash.value) ? flash.value : ''
async function ensureAudio() {
  if (url) return
  if (loadingAudio) return loadingAudio
  audioLoading.value = true
  loadingAudio = (async () => { const blob = await assistantApi.audio(props.session.id, props.file.id); if (!alive) return; url = URL.createObjectURL(blob); audio.value!.src = url; await new Promise<void>((resolve,reject) => { const el = audio.value!; el.onloadedmetadata = () => resolve(); el.onerror = () => reject(new Error()); el.load() }) })()
  try { await loadingAudio } finally { audioLoading.value = false; loadingAudio = null }
}
async function playAt(ms?: number) {
  error.value = ''
  try { await ensureAudio(); if (!alive || !audio.value) return; if (ms !== undefined) audio.value.currentTime = ms / 1000; audio.value.playbackRate = speed.value; await audio.value.play() }
  catch (e) { if (alive) error.value = e instanceof ApiError ? e.message : '录音无法播放，请重试' }
}
function toggle() { if (playing.value) audio.value?.pause(); else void playAt() }
function timeupdate() {
  if (!audio.value) return
  current.value = audio.value.currentTime * 1000
  const node = nodes.value.find(n => current.value >= (n.t_start_ms ?? 0) && current.value < (n.t_end_ms ?? 0))
  const loc = node?.loc ?? ''
  if (loc !== activeLoc.value) { activeLoc.value = loc; if (following.value && loc) locate(loc) }
}
async function download() { try { await chain; const data = await assistantApi.transcript(props.session.id, props.file.id); await saveBlob(data.blob,data.filename) } catch(e) { error.value = message(e) } }
useHotkeys({ ' ': toggle }, { withinDialog: true })
watch(() => props.session.status, () => { if (!busy.value && !pendingSaves.value) void load() })
onMounted(load)
onBeforeUnmount(() => { alive = false;  clearTimeout(timer); clearTimeout(savedTimer); audio.value?.pause(); if (url) URL.revokeObjectURL(url) })
</script>
<template>
  <div class="editor">
    <SiIsland v-if="busy" state="busy" text="正在重新计算…" />
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div class="player"><el-button :loading="audioLoading" @click="toggle">{{ playing ? '暂停' : '播放' }}</el-button><span class="num">{{ stamp(current) }} / {{ stamp(duration) }}</span><el-slider :model-value="current" :max="Math.max(duration,1)" :show-tooltip="false" aria-label="播放位置" @change="playAt(Number($event))" /><el-select v-model="speed" aria-label="播放速度" @change="audio && (audio.playbackRate = speed)"><el-option v-for="n in [0.75,1,1.25,1.5]" :key="n" :value="n" :label="n + 'x'" /></el-select><el-checkbox v-model="following">跟随播放</el-checkbox><span v-if="audioLoading" class="hint">正在载入录音…</span><span v-if="!readonly" class="save hint" role="status" :class="saveState">{{ notice || (saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '已保存' : '点击文字可修改，点别处自动保存') }}</span></div>
    <audio ref="audio" @play="playing = true" @pause="playing = false" @ended="playing = false" @timeupdate="timeupdate" @loadedmetadata="duration = (audio?.duration ?? 0) * 1000" />
    <div class="body">
      <div ref="paper" v-loading="loading && !nodes.length" class="si-paper transcript" @pointerdown="scrollbarPointer" @wheel.passive="following = false" @touchmove.passive="following = false" @keydown="['PageUp','PageDown','ArrowUp','ArrowDown','Home','End'].includes($event.key) && (following = false)">
        <p v-if="!loading && !nodes.length" class="hint">逐字稿暂无可显示的内容</p>
        <section v-for="para in paras" v-memo="[para, rev, activeIn(para), flashIn(para), readonly, editLoc, speakerEditLoc === para[0]!.loc, speakers]" :key="para[0]!.loc" :data-loc="para[0]!.loc" class="utterance" :class="{ current: !!activeIn(para) }">
          <div class="meta"><span v-if="readonly">{{ speakers[String(para[0]!.speaker)] || `发言人 ${para[0]!.speaker}` }}</span><el-select v-else-if="speakerEditLoc === para[0]!.loc" :model-value="para[0]!.speaker" aria-label="发言人" @change="changeSpeaker(para, $event)" @visible-change="!$event && (speakerEditLoc = '')"><el-option v-for="id in speakerIds" :key="id" :value="Number(id)" :label="speakers[id] || `发言人 ${id}`" /></el-select><el-button v-else link class="who" :aria-label="`修改 ${stamp(para[0]!.t_start_ms ?? 0)} 起这一段的发言人`" @click="openSpeaker(para[0]!.loc)">{{ speakers[String(para[0]!.speaker)] || `发言人 ${para[0]!.speaker}` }}<Pencil :size="12" :stroke-width="1.75" class="pen" /></el-button><span class="num">{{ stamp(para[0]!.t_start_ms ?? 0) }}</span><el-button link :aria-label="`从 ${stamp(para[0]!.t_start_ms ?? 0)} 播放`" @click="playAt(para[0]!.t_start_ms ?? 0)">播放</el-button></div>
          <div class="words">
            <template v-for="node in para" :key="node.loc">
              <button v-for="m in markMap.get(node.loc) ?? []" :key="m" type="button" class="flag" :title="`录音时的标记 ${stamp(m)}，点击从这里播放`" @click="playAt(m)"><Flag :size="11" :stroke-width="1.75" />标记 <span class="num">{{ stamp(m) }}</span></button>
              <el-input v-if="editLoc === node.loc" v-model="editText" class="sent-edit" type="textarea" :autosize="{ minRows: 2 }" maxlength="2000" show-word-limit aria-label="修改这一句" @keydown="textKey($event)" @blur="commitText(node)" />
              <span v-else class="sent" :class="{ editable: !readonly, playing: activeLoc === node.loc, flash: flash === node.loc }" :data-loc="node.loc" :tabindex="readonly ? undefined : 0" @click="startEdit(node)" @keydown.enter.prevent="startEdit(node)"><span class="tip"><button type="button" class="tplay" :aria-label="`从 ${stamp(node.t_start_ms ?? 0)} 播放`" @click.stop="playAt(node.t_start_ms ?? 0)"><Play :size="10" :stroke-width="2" /><span class="num">{{ stamp(node.t_start_ms ?? 0) }}</span></button><span v-if="!readonly" class="edit-hint"><Pencil :size="11" :stroke-width="1.75" />点击修改</span></span><span v-for="(seg,i) in spans(node)" :key="i" :class="{ low: seg.low }" :title="seg.low ? '转写置信度低，请听原音核对' : undefined">{{ seg.text }}</span></span>
            </template>
          </div>
        </section>
      </div>
      <aside class="rail">
        <h3>参会人名单</h3><div class="names"><el-tag v-for="name in names" :key="name" :closable="!readonly && !busy" @close="setNames(names.filter(n => n !== name))">{{ name }}</el-tag></div><el-input v-if="!readonly" v-model="addedName" :disabled="busy" maxlength="40" placeholder="输入姓名后回车添加" aria-label="添加参会人" @keydown.enter.prevent="addName" /><p class="hint">名单中的名字会在整份逐字稿中替换为代号，并用于同音纠正</p>
        <h3>发言人</h3><div v-for="id in speakerIds" :key="id" class="speaker"><label>发言人 {{ id }} →</label><span v-if="readonly">{{ speakers[id] || `发言人 ${id}` }}</span><el-input v-else v-model="speakers[id]" :disabled="busy" maxlength="40" :aria-label="`发言人 ${id} 姓名`" list="meeting-participants" @blur="saveSpeaker(id)" @keydown.enter.prevent="saveSpeaker(id)" /></div><datalist id="meeting-participants"><option v-for="name in names" :key="name" :value="name" /></datalist>
        <template v-if="!readonly"><div class="heading"><h3>同音纠正建议 {{ suggestions.length }} 条</h3><el-button link :disabled="busy || !suggestions.length" @click="apply(suggestions)">全部采纳</el-button></div><p v-if="!suggestions.length" class="hint">没有同音纠正建议</p><div v-for="(hint,i) in suggestions" :key="i" class="suggestion"><el-button link @click="highlight(hint.loc)">{{ hint.from }} → {{ hint.to }} · {{ stamp(nodes.find(n => n.loc === hint.loc)?.t_start_ms ?? 0) }}</el-button><el-button link :disabled="busy" @click="apply([hint])">采纳</el-button></div></template>
        <div class="exports"><el-button :disabled="busy" @click="download">逐字稿 Word</el-button><el-button v-if="!readonly" type="primary" :disabled="busy || pendingSaves > 0" @click="emit('confirm')">确认脱敏</el-button><slot name="actions" /></div>
      </aside>
    </div>
  </div>
</template>
<style scoped>
.editor { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
.player { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.player :deep(.el-slider) { flex: 1; min-width: 100px; }
.player :deep(.el-select) { width: 80px; }
.save { margin-left: auto; }
.save.saved { color: var(--si-ok); }
.body { min-height: 0; flex: 1; display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 16px; }
.transcript { overflow: auto; padding: 24px 32px; }
.utterance { content-visibility: auto; contain-intrinsic-size: auto 100px; padding: 12px 16px; border-left: 2px solid transparent; }
.utterance.current { border-color: var(--si-blue); }
.meta { display: flex; align-items: center; gap: 8px; font: var(--si-font-meta); color: var(--si-paper-ink-2); }
.meta :deep(.el-select) { width: 140px; }
.who .pen { margin-left: 4px; opacity: 0; transition: opacity var(--si-dur-1) var(--si-ease); }
.who:hover .pen, .who:focus-visible .pen { opacity: .7; }
.words { font: var(--si-font-reading); white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0 0; }
.sent { position: relative; border-radius: 3px; transition: background-color var(--si-dur-1) var(--si-ease); }
.sent.editable { cursor: text; }
.sent:hover, .sent:focus-visible { background: var(--si-surface-2); outline: none; }
.sent.playing { background: color-mix(in srgb, var(--si-blue) 10%, transparent); }
.sent.flash { background: var(--si-unresolved-bg); }
.tip { position: absolute; left: 0; bottom: 100%; z-index: 3; display: none; align-items: center; gap: 8px; padding: 2px 8px; border: 1px solid var(--si-hair-strong); border-radius: 6px; background: var(--si-surface-1); font: var(--si-font-caption); color: var(--si-ink-3); white-space: nowrap; box-shadow: var(--si-shadow-1, none); }
.sent:hover > .tip, .sent:focus-visible > .tip { display: inline-flex; }
.tplay { display: inline-flex; align-items: center; gap: 4px; padding: 0; border: 0; background: none; color: var(--si-blue); cursor: pointer; font: inherit; }
.edit-hint { display: inline-flex; align-items: center; gap: 4px; }
.sent-edit { display: block; margin: 6px 0; }
.flag { display: inline-flex; align-items: center; gap: 4px; margin: 0 6px 0 0; padding: 0 6px; border: 1px solid var(--si-warn); border-radius: 999px; background: none; color: var(--si-warn-ink, var(--si-ink-2)); font: var(--si-font-caption); vertical-align: 1px; cursor: pointer; }
.low { text-decoration: underline dashed 1px var(--si-warn); text-underline-offset: 4px; }
.rail { overflow-y: auto; padding: 0 12px; }
h3 { font: var(--si-font-h3); margin: 20px 0 8px; }
.names,.speaker,.heading,.suggestion,.exports { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.speaker { margin-bottom: 8px; }
.speaker :deep(.el-input) { flex: 1; min-width: 100px; }
.suggestion { justify-content: space-between; }
.suggestion :deep(.el-button) { white-space: normal; height: auto; text-align: left; }
.exports { margin-top: 24px; }
</style>
