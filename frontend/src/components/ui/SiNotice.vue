<script setup lang="ts">
import { computed, ref } from 'vue'
import { CircleAlert, Info, ShieldAlert, TriangleAlert, X } from '@lucide/vue'

/**
 * 就地卡（澄明二版第四节，取代 el-alert）：写原因加怎么办，就地显示，可带操作按钮。
 * type：info 说明（中性，唯一可关闭）、warning 注意（琥珀）、block 发送前复检拦截（琥珀，用 hits 插槽逐处列出）、error 错误（红）；
 * success 只留给连接测试一类的通过结果。title 为一句话，默认插槽为补充说明，actions 插槽放按钮。
 */
const props = withDefaults(defineProps<{ type?: 'info' | 'warning' | 'block' | 'error' | 'success'; title?: string; description?: string; closable?: boolean }>(),
  { type: 'info', closable: false })
const emit = defineEmits<{ close: [] }>()
const open = ref(true)
const icon = computed(() => ({ info: Info, warning: TriangleAlert, block: ShieldAlert, error: CircleAlert, success: Info })[props.type])
function close() { open.value = false; emit('close') }
</script>

<template>
  <div v-if="open" class="si-notice" :class="`t-${type}`" :role="type === 'error' || type === 'block' ? 'alert' : 'status'">
    <span class="ni" aria-hidden="true"><component :is="icon" :size="16" :stroke-width="2" /></span>
    <div class="nb">
      <b v-if="title || $slots.title" class="nt"><slot name="title">{{ title }}</slot></b>
      <div v-if="description || $slots.default" class="nd" :class="{ solo: !title && !$slots.title }"><slot>{{ description }}</slot></div>
      <div v-if="$slots.hits" class="hits"><slot name="hits" /></div>
      <div v-if="$slots.actions" class="na"><slot name="actions" /></div>
    </div>
    <button v-if="closable && type === 'info'" type="button" class="nx" aria-label="关闭" @click="close"><X :size="14" :stroke-width="2" /></button>
  </div>
</template>

<style scoped>
.si-notice { --nc: var(--si-ink-3); --ns: var(--si-card-2); --nl: var(--si-hair); display: flex; align-items: flex-start; gap: 12px; padding: 12px 16px; border: 1px solid var(--nl); border-radius: var(--si-radius-md); background: var(--ns); text-align: left; }
.t-warning, .t-block { --nc: var(--si-warn-ink); --ns: var(--si-warn-soft); --nl: var(--si-warn-line); }
.t-error { --nc: var(--si-danger-ink); --ns: var(--si-danger-soft); --nl: var(--si-danger-line); }
.t-success { --nc: var(--si-ok-ink); --ns: var(--si-ok-soft); --nl: color-mix(in srgb, var(--si-ok) 30%, transparent); }
.ni { flex: none; display: grid; place-items: center; width: 28px; height: 28px; border-radius: var(--si-radius-sm); background: var(--si-surface-1); color: var(--nc); }
.nb { flex: 1; min-width: 0; padding-top: 3px; }
.nt { display: block; font: var(--si-font-body); font-weight: 600; color: var(--si-ink-1); overflow-wrap: anywhere; }
.nd { margin-top: 2px; font: var(--si-font-body); color: var(--si-ink-2); overflow-wrap: anywhere; }
.nd.solo { margin-top: 0; color: var(--si-ink-1); }
.na { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.hits { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.hits :deep(> *) { display: flex; align-items: center; gap: 10px; padding: 6px 6px 6px 10px; border-radius: var(--si-radius-sm); background: var(--si-surface-1); font: var(--si-font-body); }
.nx { flex: none; display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border: 0; border-radius: var(--si-radius-sm); background: transparent; color: var(--si-ink-4); cursor: pointer; }
.nx:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
</style>
