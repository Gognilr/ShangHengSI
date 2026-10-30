<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Ellipsis } from '@lucide/vue'
import { assistantApi, type SessionItem } from '@/api/assistant'
import { ApiError } from '@/api/http'
import { useSessionsStore } from '@/stores/sessions'

const store = useSessionsStore()
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
    if (item.id === current.value) await router.push('/assistant')
    await store.refresh()
  } catch (e) { fail(e) }
}

function onCommand(item: SessionItem, cmd: string) { if (cmd === 'rename') rename(item); else if (cmd === 'remove') remove(item) }
</script>

<template>
  <nav class="sessions" aria-label="近期会话">
    <h2 class="heading">近期会话</h2>
    <ul v-if="store.items.length">
      <li v-for="item in store.items" :key="item.id" class="row" :class="{ current: item.id === current }">
        <router-link :to="`/assistant/${item.id}`" class="link" :title="item.title" :aria-current="item.id === current ? 'page' : undefined">
          <span v-if="item.generating" class="dot" aria-label="生成中"></span>
          <span class="title">{{ item.title }}</span>
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
    <p v-else-if="!store.loading" class="empty">暂无会话</p>
    <button v-if="store.items.length < store.total" type="button" class="more-link" :disabled="store.loading" @click="store.loadMore">查看更多</button>
  </nav>
</template>

<style scoped>
.sessions { padding: 0 8px; }
.heading { margin: 0; padding: 8px 12px 4px; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
ul { margin: 0; padding: 0; list-style: none; }
.row { position: relative; display: flex; align-items: center; border-radius: var(--el-border-radius-base); }
.row:hover { background: var(--el-fill-color-light); }
.row.current { background: var(--el-color-primary-light-9); }
.link { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; padding: 6px 4px 6px 12px; color: var(--el-text-color-regular); text-decoration: none; font: var(--si-font-body); }
.row.current .link { color: var(--el-color-primary); font-weight: 600; }
.title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--el-color-primary); }
.more { flex: none; display: grid; place-items: center; width: 28px; height: 28px; margin-right: 2px; padding: 0; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--el-text-color-secondary); cursor: pointer; opacity: 0; }
.row:hover .more, .row:focus-within .more, .more[aria-expanded="true"] { opacity: 1; }
.more:hover { background: var(--el-fill-color); color: var(--el-text-color-primary); }
.empty { margin: 0; padding: 6px 12px; font: var(--si-font-meta); color: var(--el-text-color-placeholder); }
.more-link { margin: 4px 0 0; padding: 6px 12px; border: 0; background: transparent; color: var(--el-color-primary); font: var(--si-font-meta); cursor: pointer; }
.more-link:disabled { color: var(--el-text-color-placeholder); cursor: default; }
</style>
