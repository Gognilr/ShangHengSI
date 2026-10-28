<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { assistantApi, type Session, type SessionItem, type Message, type Template } from '@/api/assistant'
import { ApiError } from '@/api/http'
import SessionList from '@/components/assistant/SessionList.vue'
import Conversation from '@/components/assistant/Conversation.vue'
import Composer from '@/components/assistant/Composer.vue'
import FilePanel from '@/components/assistant/FilePanel.vue'
const route = useRoute(), router = useRouter()
const id = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')
const sessions = ref<SessionItem[]>([]), total = ref(0), page = ref(1), query = ref(''), listLoading = ref(false), listError = ref('')
const session = ref<Session | null>(null), messages = ref<Message[]>([]), template = ref<Template | null>(null)
const loading = ref(false), error = ref(''), working = ref(false)
const offsets = new Map<number, number>()
let active = true, version = 0, listVersion = 0, polling = false, timer: ReturnType<typeof setTimeout> | undefined
function failure(e: unknown) { return e instanceof ApiError ? e.status === 404 ? '任务不存在或已被删除' : e.message : '加载失败，请刷新页面后重试' }
async function list() {
  const current = ++listVersion
  listLoading.value = true; listError.value = ''
  try { const result = await assistantApi.list(page.value, query.value); if (active && current === listVersion) { sessions.value = result.items; total.value = result.total } }
  catch (e) { if (active && current === listVersion) listError.value = failure(e) }
  finally { if (active && current === listVersion) listLoading.value = false }
}
function schedule() {
  clearTimeout(timer)
  if (active && session.value && (session.value.status === 'parsing' || messages.value.some(m => ['queued', 'streaming'].includes(m.status)))) timer = setTimeout(poll, 1000)
}
async function load() {
  const current = ++version, selected = id.value
  clearTimeout(timer); session.value = null; messages.value = []; offsets.clear(); template.value = null
  loading.value = !!selected; error.value = ''
  await list()
  if (!selected || !active || current !== version) { loading.value = false; return }
  try {
    const [detail, response] = await Promise.all([assistantApi.get(selected), assistantApi.messages(selected)])
    if (!active || current !== version) return
    session.value = detail
    messages.value = response.items.map(item => ['queued', 'streaming'].includes(item.status) ? { ...item, content: '' } : item)
  } catch (e) { if (active && current === version) error.value = failure(e) }
  finally { if (active && current === version) { loading.value = false; schedule() } }
}
async function poll() {
  if (!active || !session.value || polling) { schedule(); return }
  polling = true
  const current = version, selected = id.value
  try {
    let finished = false
    for (const item of messages.value.filter(m => ['queued', 'streaming'].includes(m.status))) {
      const delta = await assistantApi.delta(selected, item.id, offsets.get(item.id) ?? 0)
      if (!active || current !== version) return
      item.content += delta.text_from_offset; item.status = delta.status; item.error = delta.error
      if (delta.compare) item.compare = delta.compare
      offsets.set(item.id, delta.offset)
      if (item.template === 'translate' && ['queued', 'streaming'].includes(delta.status)) {
        // 只补读当前译文的结构化进度，不重复拉取整段会话历史；正文仍按增量偏移追加。
        const fresh = (await assistantApi.messages(selected, item.id - 1)).items.find(row => row.id === item.id)
        if (!active || current !== version) return
        if (fresh) { item.extras = fresh.extras; item.translation = fresh.translation; item.translation_notice = fresh.translation_notice }
      }
      if (!['queued', 'streaming'].includes(delta.status)) finished = true
    }
    if (finished) {
      const response = await assistantApi.messages(selected)
      if (!active || current !== version) return
      messages.value = response.items
    }
    const detail = await assistantApi.get(selected)
    if (!active || current !== version) return
    session.value = detail; error.value = ''
    if (finished || detail.status !== 'parsing') await list()
  } catch (e) {
    if (active && current === version) {
      error.value = failure(e)
      if (e instanceof ApiError && e.status === 404) { session.value = null; return }
    }
  } finally { polling = false; if (active && current === version) schedule() }
}
async function refresh() {
  const selected = id.value, current = version
  try {
    const [detail, response] = await Promise.all([assistantApi.get(selected), assistantApi.messages(selected)])
    if (!active || current !== version) return
    session.value = detail; messages.value = response.items.map(item => {
      const previous = messages.value.find(old => old.id === item.id)
      return ['queued', 'streaming'].includes(item.status) ? { ...item, content: previous?.content ?? '' } : item
    })
    error.value = ''; await list(); schedule()
  } catch (e) { if (active && current === version) error.value = failure(e) }
}
async function create() {
  if (working.value) return
  working.value = true
  try { const result = await assistantApi.create(); await router.push(`/assistant/${result.id}`) }
  catch (e) { error.value = failure(e) }
  finally { working.value = false }
}
async function remove(item: SessionItem) {
  try { await ElMessageBox.confirm(`将删除“${item.title}”及其文件、消息与对照表，无法恢复。`, '删除会话', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try { await assistantApi.remove(item.id); if (item.id === id.value) await router.push('/assistant'); else await list() }
  catch (e) { error.value = failure(e) }
}
async function rename() {
  if (!session.value) return
  try {
    const { value } = await ElMessageBox.prompt('输入会话标题，最多 60 字', '修改会话标题', { inputValue: session.value.title, inputValidator: value => !!value.trim() && value.trim().length <= 60 || '请输入 1～60 字标题', confirmButtonText: '保存', cancelButtonText: '取消' })
    session.value = await assistantApi.patch(id.value, { title: value }); await list()
  } catch (e) { if (e instanceof ApiError) error.value = e.message }
}
async function stop(mid: number) { try { await assistantApi.stop(id.value, mid); await refresh() } catch (e) { error.value = failure(e) } }
async function retry(mid: number) { try { await assistantApi.retry(id.value, mid); await refresh() } catch (e) { error.value = failure(e) } }
async function search(q: string) { query.value = q; page.value = 1; await list() }
async function changePage(value: number) { page.value = value; await list() }
watch(id, load, { immediate: true })
onBeforeUnmount(() => { active = false; ++version; clearTimeout(timer); offsets.clear() })
</script>
<template>
  <div class="page page--split assistant-page">
    <SessionList class="sidebar" :items="sessions" :selected="id" :total="total" :page="page" :loading="listLoading" :error="listError" @create="create" @select="router.push(`/assistant/${$event}`)" @remove="remove" @search="search" @page="changePage" />
    <main v-loading="loading" class="dialogue">
      <el-alert v-if="error" type="error" :closable="false" :title="error" />
      <template v-if="session">
        <div class="toolbar"><el-button class="title" link @click="rename">{{ session.title }}</el-button><el-button link type="danger" :disabled="working" @click="remove(session)">删除会话</el-button></div>
        <Conversation :messages="messages" :session-id="id" :template="template" @template="template = $event" @stop="stop" @retry="retry" />
        <Composer :key="id" v-model:template="template" :session="session" @sent="refresh" />
      </template>
      <el-empty v-else-if="!loading" description="选择一个会话，或新建会话加入文件后开始"><el-button type="primary" :loading="working" @click="create">新建会话</el-button></el-empty>
    </main>
    <FilePanel v-if="session" :key="id" class="file-sidebar" :session="session" @refresh="refresh" />
    <aside v-else class="file-sidebar empty-files"><p class="hint">发给模型的只有脱敏后的文字，回复在本机还原</p></aside>
  </div>
</template>
<style scoped>
.assistant-page { height: calc(100vh - 64px); max-height: 100%; box-sizing: border-box; padding-top: 20px; padding-bottom: 20px; display: grid; grid-template-columns: 264px minmax(0, 1fr) 320px; gap: 20px; overflow: hidden; }
.sidebar, .dialogue, .file-sidebar { min-width: 0; min-height: 0; background: var(--el-bg-color); border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.dialogue { display: flex; flex-direction: column; overflow: hidden; }
.toolbar { display: flex; gap: 16px; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--el-border-color-light); }
.title { min-width: 0; white-space: normal; overflow-wrap: anywhere; text-align: left; font: var(--si-font-h2); }
.empty-files { padding: 20px 16px; }
</style>
