<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { assistantApi, type Session, type SessionFile, type Suggestion } from '@/api/assistant'
import type { PreviewNode } from '@/api/preview'
import { ApiError } from '@/api/http'
import { saveBlob } from '@/utils/save'
import { useHotkeys } from '@/utils/hotkeys'
import SiIsland from '@/components/ui/SiIsland.vue'
const props = defineProps<{ session: Session; file: SessionFile }>()
const emit = defineEmits<{ refresh: []; confirm: [] }>()
const nodes = ref<PreviewNode[]>([]), suggestions = ref<Suggestion[]>([]), error = ref(''), loading = ref(false), busy = ref(false)
const names = ref<string[]>([...props.session.participants]), addedName = ref(''), speakers = ref<Record<string,string | null>>({ ...props.file.speakers })
const readonly = computed(() => !!props.file.confirmed_at || props.session.original_deleted)
const speakerIds = computed(() => [...new Set([...Object.keys(speakers.value), ...nodes.value.map(n => String(n.speaker))])].filter(k => /^\d+$/.test(k)).sort((a,b) => Number(a)-Number(b)))
const audio = ref<HTMLAudioElement>(), paper = ref<HTMLElement>(), playing = ref(false), current = ref(0), duration = ref(props.file.media?.duration_ms ?? 0), speed = ref(1), following = ref(true), audioLoading = ref(false), activeLoc = ref('')
const speakerEditLoc = ref('')
const editLoc = ref(''), editText = ref(''), flash = ref('')
let loadVersion = 0
let url = '', alive = true, loadingAudio: Promise<void> | null = null, timer: ReturnType<typeof setTimeout> | undefined
function message(e: unknown) { return e instanceof ApiError ? e.message : '读取或保存失败，请重试' }
function stamp(ms = 0) { const s = Math.floor(ms / 1000); return [Math.floor(s/3600), Math.floor(s/60)%60, s%60].map(v => String(v).padStart(2,'0')).join(':') }
async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const [result, hints, detail] = await Promise.all([assistantApi.nodes(props.session.id, props.file.id), readonly.value ? Promise.resolve([]) : assistantApi.suggestions(props.session.id, props.file.id), assistantApi.get(props.session.id)])
    if (!alive || version !== loadVersion) return
    nodes.value = result.items.filter(n => n.kind === 'utterance')
    suggestions.value = hints; names.value = detail.participants
    speakers.value = { ...detail.files.find(f => f.id === props.file.id)?.speakers }
  } catch (e) { if (alive && version === loadVersion) error.value = message(e) }
  finally { if (alive && version === loadVersion) loading.value = false }
}
async function mutate(action: () => Promise<unknown>) {
  if (readonly.value || busy.value) return
  busy.value = true; error.value = ''
  try { await action(); if (alive) { await load(); emit('refresh') } }
  catch (e) { if (alive) { error.value = message(e); await load() } }
  finally { if (alive) busy.value = false }
}
function saveText(node: PreviewNode) { const text = editText.value; editLoc.value = ''; return mutate(() => assistantApi.utterance(props.session.id, props.file.id, node.loc, { text })) }
function textKey(event: KeyboardEvent, node: PreviewNode) {
  if (event.isComposing) return
  if (event.key === 'Escape') { event.preventDefault(); editLoc.value = '' }
  else if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void saveText(node) }
}
async function openSpeaker(node: PreviewNode) { speakerEditLoc.value = node.loc; await nextTick(); paper.value?.querySelector<HTMLElement>('.meta .el-select')?.click() }
function changeSpeaker(node: PreviewNode, speaker: number) { return mutate(() => assistantApi.utterance(props.session.id, props.file.id, node.loc, { speaker })) }
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
function highlight(loc: string) { locate(loc); flash.value = loc; clearTimeout(timer); timer = setTimeout(() => { flash.value = '' }, 1200) }
function spans(node: PreviewNode) {
  const chars = Array.from(node.text ?? ''), out: { text: string; low: boolean }[] = []
  chars.forEach((c,i) => { const low = (node.low_conf_spans ?? []).some(([a,b]) => i >= a! && i < b!); const previous = out[out.length-1]; if (previous?.low === low) previous.text += c; else out.push({ text:c, low }) })
  return out
}
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
async function download() { try { const data = await assistantApi.transcript(props.session.id, props.file.id); await saveBlob(data.blob,data.filename) } catch(e) { error.value = message(e) } }
useHotkeys({ ' ': toggle }, { withinDialog: true })
watch(() => props.session.status, () => { if (!busy.value) void load() })
onMounted(load)
onBeforeUnmount(() => { alive = false;  clearTimeout(timer); audio.value?.pause(); if (url) URL.revokeObjectURL(url) })
async function edit(node: PreviewNode) { if (readonly.value || busy.value) return; editLoc.value = node.loc; editText.value = node.text ?? ''; await nextTick(); paper.value?.querySelector<HTMLTextAreaElement>('textarea')?.focus() }
</script>
<template>
  <div class="editor">
    <SiIsland v-if="busy" state="busy" text="正在重新计算…" />
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div class="player"><el-button :loading="audioLoading" @click="toggle">{{ playing ? '暂停' : '播放' }}</el-button><span class="num">{{ stamp(current) }} / {{ stamp(duration) }}</span><el-slider :model-value="current" :max="Math.max(duration,1)" :show-tooltip="false" aria-label="播放位置" @change="playAt(Number($event))" /><el-select v-model="speed" aria-label="播放速度" @change="audio && (audio.playbackRate = speed)"><el-option v-for="n in [0.75,1,1.25,1.5]" :key="n" :value="n" :label="n + 'x'" /></el-select><el-checkbox v-model="following">跟随播放</el-checkbox><span v-if="audioLoading" class="hint">正在载入录音…</span></div>
    <audio ref="audio" @play="playing = true" @pause="playing = false" @ended="playing = false" @timeupdate="timeupdate" @loadedmetadata="duration = (audio?.duration ?? 0) * 1000" />
    <div class="body">
      <div ref="paper" v-loading="loading" class="si-paper transcript" @pointerdown="scrollbarPointer" @wheel.passive="following = false" @touchmove.passive="following = false" @keydown="['PageUp','PageDown','ArrowUp','ArrowDown','Home','End'].includes($event.key) && (following = false)">
        <p v-if="!loading && !nodes.length" class="hint">逐字稿暂无可显示的内容</p>
        <section v-for="node in nodes" v-memo="[node, activeLoc === node.loc, flash === node.loc, readonly, busy, editLoc === node.loc, speakers, speakerEditLoc === node.loc]" :key="node.loc" :data-loc="node.loc" class="utterance" :class="{ current: activeLoc === node.loc, flash: flash === node.loc }">
          <div class="meta"><span v-if="readonly">{{ speakers[String(node.speaker)] || `发言人 ${node.speaker}` }}</span><el-select v-else-if="speakerEditLoc === node.loc" :model-value="node.speaker" :disabled="busy" aria-label="发言人" @change="changeSpeaker(node, $event); speakerEditLoc = ''"><el-option v-for="id in speakerIds" :key="id" :value="Number(id)" :label="speakers[id] || `发言人 ${id}`" /></el-select><el-button v-else link :disabled="busy" :aria-label="`修改 ${node.loc} 的发言人`" @click="openSpeaker(node)">{{ speakers[String(node.speaker)] || `发言人 ${node.speaker}` }}</el-button><span class="num">{{ stamp(node.t_start_ms ?? 0) }}</span><el-button link :aria-label="`从 ${stamp(node.t_start_ms ?? 0)} 播放`" @click="playAt(node.t_start_ms ?? 0)">播放</el-button></div>
          <el-input v-if="editLoc === node.loc" v-model="editText" type="textarea" :autosize="{minRows:2}" maxlength="2000" show-word-limit @keydown="textKey($event, node)" />
          <p v-else class="words" :tabindex="readonly ? undefined : 0" @click="edit(node)" @keydown.enter.prevent="edit(node)"><span v-for="(seg,i) in spans(node)" :key="i" :class="{ low: seg.low }" :title="seg.low ? '转写置信度低，请听原音核对' : undefined">{{ seg.text }}</span></p>
        </section>
      </div>
      <aside class="rail">
        <h3>参会人名单</h3><div class="names"><el-tag v-for="name in names" :key="name" :closable="!readonly && !busy" @close="setNames(names.filter(n => n !== name))">{{ name }}</el-tag></div><el-input v-if="!readonly" v-model="addedName" :disabled="busy" maxlength="40" placeholder="输入姓名后回车添加" aria-label="添加参会人" @keydown.enter.prevent="addName" /><p class="hint">名单中的名字会在整份逐字稿中替换为代号，并用于同音纠正</p>
        <h3>发言人</h3><div v-for="id in speakerIds" :key="id" class="speaker"><label>发言人 {{ id }} →</label><span v-if="readonly">{{ speakers[id] || `发言人 ${id}` }}</span><el-input v-else v-model="speakers[id]" :disabled="busy" maxlength="40" :aria-label="`发言人 ${id} 姓名`" list="meeting-participants" @blur="saveSpeaker(id)" @keydown.enter.prevent="saveSpeaker(id)" /></div><datalist id="meeting-participants"><option v-for="name in names" :key="name" :value="name" /></datalist>
        <template v-if="!readonly"><div class="heading"><h3>同音纠正建议 {{ suggestions.length }} 条</h3><el-button link :disabled="busy || !suggestions.length" @click="apply(suggestions)">全部采纳</el-button></div><p v-if="!suggestions.length" class="hint">没有同音纠正建议</p><div v-for="(hint,i) in suggestions" :key="i" class="suggestion"><el-button link @click="highlight(hint.loc)">{{ hint.from }} → {{ hint.to }} · {{ stamp(nodes.find(n => n.loc === hint.loc)?.t_start_ms ?? 0) }}</el-button><el-button link :disabled="busy" @click="apply([hint])">采纳</el-button></div></template>
        <div class="exports"><el-button :disabled="busy" @click="download">逐字稿 Word</el-button><el-button v-if="!readonly" type="primary" :disabled="busy" @click="emit('confirm')">去确认脱敏</el-button></div>
      </aside>
    </div>
  </div>
</template>
<style scoped>
.editor { height: calc(100vh - 100px); display: flex; flex-direction: column; gap: 12px; }
.player { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.player :deep(.el-slider) { flex: 1; min-width: 100px; }
.player :deep(.el-select) { width: 80px; }
.body { min-height: 0; flex: 1; display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 16px; }
.transcript { overflow: auto; padding: 24px 32px; }
.utterance { content-visibility: auto; contain-intrinsic-size: auto 100px; padding: 12px 16px; border-left: 2px solid transparent; }
.utterance.current { border-color: var(--si-blue); }
.utterance.flash { background: var(--si-unresolved-bg); }
.meta { display: flex; align-items: center; gap: 8px; font: var(--si-font-meta); color: var(--si-paper-ink-2); }
.meta :deep(.el-select) { width: 140px; }
.words { font: var(--si-font-reading); white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0 0; }
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
