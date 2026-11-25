<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Ellipsis } from '@lucide/vue'
import { assistantApi, isReviewSession, sessionLocation, type SessionItem } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { useSessionsStore } from '@/stores/sessions'

import { useAuthStore } from '@/stores/auth'

const store = useSessionsStore(), auth = useAuthStore()
const route = useRoute(), router = useRouter()
const current = computed(() => typeof route.params.sessionId === 'string' ? route.params.sessionId : '')

function fail(e: unknown) { ElMessage.error(e instanceof ApiError ? e.status === 404 ? '会话不存在或已被删除' : e.message : '操作失败，请稍后重试') }

async function rename(item: SessionItem) {
  let value: string
  try {
    ({ value } = await ElMessageBox.prompt('输入会话标题，最多 60 字', '修改会话标题', { inputValue: item.title, inputValidator: v => !!v.trim() && v.trim().length <= 60 || '请输入 1～60 字标题', confirmButtonText: '保存', cancelButtonText: '取消' }))
  } catch { return }
  try { await assistantApi.patch(item.id, { title: value }); await store.refresh() } catch (e) { fail(e) }
}

async function remove(item: SessionItem) {
  try { await ElMessageBox.confirm(`将删除“${item.title}”及其文件、消息与对照表，无法恢复。`, '删除会话', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try {
    await assistantApi.remove(item.id)
    if (item.id === current.value) await router.push(auth.can('use.assistant') ? '/assistant' : '/review')
    await store.refresh()
  } catch (e) { fail(e) }
}

function onCommand(item: SessionItem, cmd: string) { if (cmd === 'rename') rename(item); else if (cmd === 'remove') remove(item) }

// 澄明 8.0：最多 8 条，每条带模块色圆点（发起过审核的为文件审核色，其余为衡青）；更多的去历史记录
const MAX = 8
const shown = computed(() => store.items.filter(item => auth.can('use.assistant') || isReviewSession(item)).slice(0, MAX))
const more = computed(() => store.total > MAX)
</script>

<template>
  <nav class="sessions" aria-label="近期会话">
    <h2 v-if="shown.length" class="heading">近期会话</h2>
    <ul v-if="shown.length">
      <li v-for="item in shown" :key="item.id" class="row" :class="{ current: item.id === current }">
        <router-link :to="sessionLocation(item, auth.can('use.review'))" class="link" :title="item.title" :aria-current="item.id === current ? 'page' : undefined">
          <i class="dot" :class="{ review: auth.can('use.review') && isReviewSession(item), 'si-breath': item.generating }" :aria-label="item.generating ? '生成中' : undefined" />
          <span class="title">{{ item.title }}</span>
          <span v-if="item.reviewing" class="tag tag--busy">审核中</span>
          <span v-else-if="store.finished.has(item.id) && item.id !== current" class="tag tag--done">审核完成</span>
        </router-link>
        <el-dropdown trigger="click" placement="bottom-end" @command="onCommand(item, $event)">
          <button type="button" class="more" aria-label="会话操作" :title="`“${item.title}”的操作`"><Ellipsis :size="16" :stroke-width="1.75" /></button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="rename">重命名</el-dropdown-item>
              <el-dropdown-item command="remove" divided>删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </li>
    </ul>
    <router-link v-if="more" to="/history" class="more-link">全部历史</router-link>
  </nav>
</template>

<style scoped>
.heading { margin: 0; padding: 14px 12px 6px; font: var(--si-font-caption); font-weight: 500; color: var(--si-ink-4); }
ul { margin: 0; padding: 0; list-style: none; }
.row { position: relative; display: flex; align-items: center; border-radius: 8px; transition: background-color var(--si-d2) var(--si-ease-out); }
.row:hover { background: var(--si-hover-bg); }
.row.current { background: var(--si-select-bg); }
.link { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 4px 0 12px; color: var(--si-ink-2); text-decoration: none; font: var(--si-font-body); font-size: calc(13.5px * var(--si-fs)); }
.row.current .link { color: var(--si-ink-1); font-weight: 600; }
.title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--si-brand-2); }
.dot.review { background: var(--si-m-review); }
.tag { flex: none; padding: 0 6px; border-radius: 999px; font: var(--si-font-badge); }
.tag--busy { color: var(--si-info-ink); background: var(--si-info-soft); }
.tag--done { color: var(--si-blue-ink); background: var(--si-blue-tint); }
.more { flex: none; display: grid; place-items: center; width: 28px; height: 28px; margin-right: 3px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: var(--si-ink-3); cursor: pointer; opacity: 0; transition: opacity var(--si-d2) var(--si-ease-out), background-color var(--si-d2) var(--si-ease-out); }
.row:hover .more, .row:focus-within .more, .more[aria-expanded="true"] { opacity: 1; }
.more:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.more-link { display: block; margin: 4px 0 0; padding: 6px 12px; color: var(--si-ink-3); font: var(--si-font-meta); text-decoration: none; }
.more-link:hover { color: var(--si-blue-ink); }
</style>
