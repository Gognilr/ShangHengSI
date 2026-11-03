<script setup lang="ts">
// 管理员提醒（SPEC 6.7）：侧栏“提醒”弹层的内容；提醒文字原样显示，“去处理”只链到本人有权限的设置分页
import { computed } from 'vue'
import type { AdminAlert } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ navigate: [] }>()
const auth = useAuthStore()
const TARGET: Record<string, { path: string; scope: 'sys' | 'biz' }> = {
  BACKUP_FAILED: { path: '/settings/data', scope: 'sys' },
  BACKUP_SPACE_LOW: { path: '/settings/data', scope: 'sys' },
  RETENTION_REVIEW: { path: '/settings/data', scope: 'sys' },
  LAWSYNC_FAILED: { path: '/settings/laws', scope: 'biz' },
  LAW_IMPORT_PENDING: { path: '/settings/laws', scope: 'biz' },
  CALL_CAP_NEAR: { path: '/settings/model', scope: 'sys' },
  PROMPT_TERM_CONFLICT: { path: '/settings/redaction', scope: 'biz' },
}
function target(alert: AdminAlert) {
  const t = TARGET[alert.code ?? '']
  return t && (t.scope === 'sys' ? auth.canSysAdmin : auth.canBizAdmin) ? t.path : null
}
const items = computed(() => auth.visibleAlerts.map(alert => ({ alert, to: target(alert), key: JSON.stringify([alert.code, alert.message]) })))
</script>

<template>
  <div class="alert-list" role="region" aria-label="管理员提醒">
    <div class="head">需要处理 · {{ items.length }}</div>
    <TransitionGroup name="si-list" tag="ul">
      <li v-for="item in items" :key="item.key">
        <span class="dot" aria-hidden="true" />
        <div class="body">
          <p>{{ item.alert.message }}</p>
          <div class="actions">
            <router-link v-if="item.to" :to="item.to" class="go" @click="emit('navigate')">去处理</router-link>
            <button type="button" class="later" @click="auth.dismissAlert(item.alert)">本次收起</button>
          </div>
        </div>
      </li>
    </TransitionGroup>
    <p class="note">收起只在本次打开期间有效，刷新后仍会提醒，处理后自动消失。</p>
  </div>
</template>

<style scoped>
.alert-list { display: flex; flex-direction: column; gap: 8px; }
.head { font: var(--si-font-label); color: var(--si-ink-1); }
ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
li { display: flex; gap: 10px; padding: 10px; border-radius: var(--el-border-radius-base); background: var(--si-surface-2); }
.dot { flex: none; width: 6px; height: 6px; margin-top: 7px; border-radius: 50%; background: var(--si-warn); }
.body { min-width: 0; flex: 1; }
p { margin: 0; font: var(--si-font-body); color: var(--si-ink-1); overflow-wrap: anywhere; }
.actions { display: flex; gap: 16px; margin-top: 6px; }
.go, .later { padding: 0; border: 0; background: none; font: var(--si-font-meta); cursor: pointer; text-decoration: none; }
.go { color: var(--si-blue-ink); }
.later { color: var(--si-ink-3); }
.go:hover, .later:hover { text-decoration: underline; }
.note { margin: 0; font: var(--si-font-caption); color: var(--si-ink-4); }
</style>
