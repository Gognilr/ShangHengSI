<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import type { LocalFinding } from '@/api/localFindings'
import EmptyState from '@/components/EmptyState.vue'
import ZoneHeader from '@/components/ui/ZoneHeader.vue'
import { localCheckLabel, SEVERITY_ORDER, SEVERITY_TAG, severityLabel } from '@/utils/labels'

/**
 * 本地校验列表（V31-U3 第二节，审核任务与助手会话共用）：本地计算 · 结果确定 与 无法核验（L-INFO）分两组。
 * loader 只在 loadKey 变化时调用，父级不必保证 loader 是同一个函数。
 */
const props = defineProps<{ loader: () => Promise<{ items: LocalFinding[] }>; loadKey: string; readonlyLocations?: boolean }>()
const emit = defineEmits<{
  (e: 'count', count: number | null): void
  (e: 'select', locs: string[]): void
}>()
const items = ref<LocalFinding[]>([])
const loading = ref(false)
const error = ref('')
const showUnverified = ref(false)
let version = 0

const order = (a: LocalFinding, b: LocalFinding) =>
  (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) || a.check_code.localeCompare(b.check_code, 'en', { numeric: true })
const certain = computed(() => items.value.filter(f => f.check_code !== 'L-INFO').sort(order))
const unverified = computed(() => items.value.filter(f => f.check_code === 'L-INFO'))

async function load() {
  const current = ++version
  loading.value = true
  error.value = ''
  items.value = []
  showUnverified.value = false
  emit('count', null)
  try {
    const result = await props.loader()
    if (current !== version) return
    items.value = result.items
    emit('count', result.items.length)
  } catch (e) {
    if (current !== version) return
    error.value = e instanceof ApiError
      ? e.status === 404 ? '内容不存在或已被删除' : `${e.message}，请刷新页面后重试`
      : '加载失败，请刷新页面后重试'
  } finally {
    if (current === version) loading.value = false
  }
}

watch(() => props.loadKey, load, { immediate: true })
defineExpose({ reload: load })
</script>

<template>
  <div v-loading="loading" class="local-check-list">
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
    <EmptyState v-else-if="!loading && !items.length" compact title="本地校验未发现问题" />
    <template v-else-if="items.length">
      <ZoneHeader kind="local" :count="certain.length" />
      <p v-if="!certain.length" class="none">本地校验未发现问题</p>
      <ul class="cards">
        <li v-for="f in certain" :key="f.id" class="card">
          <div class="head">
            <el-tag :type="SEVERITY_TAG[f.severity] ?? 'info'" size="small" round>{{ severityLabel(f.severity) }}</el-tag>
            <span class="name">{{ localCheckLabel(f.check_code) }}</span>
          </div>
          <el-button v-if="f.locs.length && !readonlyLocations" link type="primary" class="location" @click="emit('select', [...f.locs])">{{ f.location || '定位' }}</el-button>
          <div v-else-if="f.location" class="location plain">{{ f.location }}</div>
          <p class="detail">{{ f.detail }}</p>
        </li>
      </ul>
      <template v-if="unverified.length">
        <el-button v-if="!showUnverified" link class="unverified-toggle" @click="showUnverified = true">无法核验 {{ unverified.length }} 项 · 展开</el-button>
        <template v-else>
          <ZoneHeader kind="unverified" :count="unverified.length"><el-button link size="small" @click="showUnverified = false">收起</el-button></ZoneHeader>
          <ul class="cards">
            <li v-for="f in unverified" :key="f.id" class="card muted">
              <el-button v-if="f.locs.length && !readonlyLocations" link type="primary" class="location" @click="emit('select', [...f.locs])">{{ f.location || '定位' }}</el-button>
              <div v-else-if="f.location" class="location plain">{{ f.location }}</div>
              <p class="detail">{{ f.detail }}</p>
            </li>
          </ul>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.local-check-list { display: flex; flex-direction: column; gap: 8px; min-height: 64px; }
.cards { display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 0; list-style: none; }
.card { padding: 10px 12px; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); }
.card.muted { background: transparent; }
.head { display: flex; align-items: center; gap: 8px; }
.name { font: var(--si-font-h4); color: var(--el-text-color-primary); }
.location { display: block; height: auto; margin-top: 4px; padding: 0; font: var(--si-font-meta); text-align: left; white-space: normal; overflow-wrap: anywhere; }
.location.plain { color: var(--si-ink-3); }
.detail { margin: 4px 0 0; font: var(--si-font-body); color: var(--si-ink-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.none { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.unverified-toggle { align-self: flex-start; color: var(--si-ink-3); }
</style>
