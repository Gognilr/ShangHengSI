<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SessionItem } from '@/api/assistant'
import { formatDate } from '@/utils/time'
const props = defineProps<{ items: SessionItem[]; selected: string; total: number; page: number; loading: boolean; error: string }>()
const emit = defineEmits<{ select: [id: string]; create: []; remove: [item: SessionItem]; search: [q: string]; page: [page: number] }>()
const query = ref('')
const groups = computed(() => {
  const today = formatDate(new Date()), yesterday = formatDate(new Date(Date.now() - 86400000))
  return ['今天', '昨天', '更早'].map(label => ({ label, items: props.items.filter(item => {
    const date = formatDate(item.last_activity_at)
    return (date === today ? '今天' : date === yesterday ? '昨天' : '更早') === label
  }) }))
})
</script>
<template>
  <aside v-loading="loading" class="session-list">
    <el-button type="primary" @click="emit('create')">新建会话</el-button>
    <el-input v-model="query" placeholder="搜索会话" aria-label="搜索会话" clearable @keyup.enter="emit('search', query)" @clear="emit('search', '')"><template #append><el-button @click="emit('search', query)">搜索</el-button></template></el-input>
    <el-alert v-if="error" type="error" :closable="false" :title="error" />
    <div class="session-scroll">
      <section v-for="group in groups" :key="group.label">
        <p v-if="group.items.length" class="hint">{{ group.label }}</p>
        <div v-for="item in group.items" :key="item.id" class="session-row" :class="{ selected: selected === item.id }">
          <button class="session-title" @click="emit('select', item.id)"><span v-if="item.generating" class="dot" aria-label="生成中">●</span>{{ item.title }}</button>
          <el-button class="delete" link type="danger" @click="emit('remove', item)">删除</el-button>
        </div>
      </section>
      <el-empty v-if="!loading && !items.length" description="还没有会话，点击“新建会话”开始" />
    </div>
    <el-pagination v-if="total > 30" small layout="prev, pager, next" :total="total" :page-size="30" :current-page="page" @current-change="emit('page', $event)" />
  </aside>
</template>
<style scoped>
.session-list { display: flex; flex-direction: column; gap: 16px; padding: 20px 16px; min-height: 0; }
.session-scroll { overflow-y: auto; min-height: 0; flex: 1; }
.session-row { display: flex; gap: 8px; align-items: center; border-radius: var(--el-border-radius-base); padding: 8px; }
.session-row.selected { background: var(--el-color-primary-light-9); }
.session-title { flex: 1; min-width: 0; border: 0; background: transparent; color: var(--el-text-color-primary); text-align: left; font: var(--si-font-body); cursor: pointer; overflow-wrap: anywhere; }
.delete { opacity: 0; }
.session-row:hover .delete, .session-row:focus-within .delete { opacity: 1; }
.dot { color: var(--el-color-primary); margin-right: 8px; }
button:focus-visible { outline: 2px solid var(--si-focus-ring); }
</style>
