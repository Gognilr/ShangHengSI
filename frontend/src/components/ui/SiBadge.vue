<script setup lang="ts">
import { computed, type Component } from 'vue'
import { X } from '@lucide/vue'

/**
 * 胶囊徽标（澄明二版第四节，取代 el-tag）：浅底深字全圆，可带圆点或图标。
 * tone：neutral 中性、brand 衡青、ai 灵紫、danger 红、warn 琥珀、low 天蓝、ok 完成、module 模块色（配 module）、kind 七类脱敏（配 kind）。
 * 也接受 el-tag 的旧取值（primary / success / warning / info），迁移时不必逐处改映射表；info 是中性，不是天蓝。
 */
export type BadgeTone = 'neutral' | 'brand' | 'ai' | 'danger' | 'warn' | 'low' | 'ok' | 'module' | 'kind'
type Legacy = 'primary' | 'success' | 'warning' | 'info' | ''
const props = withDefaults(defineProps<{
  tone?: BadgeTone | Legacy; dot?: boolean; icon?: Component; plain?: boolean; closable?: boolean
  module?: 'review' | 'bid' | 'meet' | 'trans'; kind?: 'org' | 'person' | 'contact' | 'id' | 'addr' | 'amount' | 'other'
}>(), { tone: 'neutral', dot: false, plain: false, closable: false })
const emit = defineEmits<{ close: [] }>()
const LEGACY: Record<string, BadgeTone> = { primary: 'brand', success: 'ok', warning: 'warn', info: 'neutral', '': 'neutral' }
const resolved = computed<BadgeTone>(() => LEGACY[props.tone] ?? (props.tone as BadgeTone))
</script>

<template>
  <span class="si-badge" :class="[`t-${resolved}`, module && `m-${module}`, kind && `k-${kind}`, { plain }]">
    <i v-if="dot" class="d" aria-hidden="true" /><component :is="icon" v-if="icon" :size="13" :stroke-width="2" aria-hidden="true" /><slot />
    <button v-if="closable" type="button" class="x" aria-label="移除" @click.stop="emit('close')"><X :size="12" :stroke-width="2" /></button>
  </span>
</template>

<style scoped>
.si-badge { --bg: var(--si-hover-bg); --ink: var(--si-ink-3); display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 9px; border-radius: var(--si-radius-badge); background: var(--bg); color: var(--ink); font: var(--si-font-badge); white-space: nowrap; vertical-align: middle; }
.d { flex: none; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.t-brand { --bg: var(--si-blue-tint); --ink: var(--si-blue-ink); }
.t-ai { --bg: var(--si-ai-soft); --ink: var(--si-ai-ink); }
.t-danger { --bg: var(--si-danger-soft); --ink: var(--si-danger-ink); }
.t-warn { --bg: var(--si-warn-soft); --ink: var(--si-warn-ink); }
.t-low { --bg: var(--si-info-soft); --ink: var(--si-info-ink); }
.t-ok { --bg: var(--si-ok-soft); --ink: var(--si-ok-ink); }
.t-module.m-review { --bg: var(--si-m-review-soft); --ink: color-mix(in srgb, var(--si-m-review) 70%, var(--si-ink-1)); }
.t-module.m-bid { --bg: var(--si-m-bid-soft); --ink: color-mix(in srgb, var(--si-m-bid) 70%, var(--si-ink-1)); }
.t-module.m-meet { --bg: var(--si-m-meet-soft); --ink: color-mix(in srgb, var(--si-m-meet) 70%, var(--si-ink-1)); }
.t-module.m-trans { --bg: var(--si-m-trans-soft); --ink: color-mix(in srgb, var(--si-m-trans) 70%, var(--si-ink-1)); }
.t-kind.k-org { --bg: var(--si-k-org-bg); --ink: var(--si-k-org-ink); }
.t-kind.k-person { --bg: var(--si-k-person-bg); --ink: var(--si-k-person-ink); }
.t-kind.k-contact { --bg: var(--si-k-contact-bg); --ink: var(--si-k-contact-ink); }
.t-kind.k-id { --bg: var(--si-k-id-bg); --ink: var(--si-k-id-ink); }
.t-kind.k-addr { --bg: var(--si-k-addr-bg); --ink: var(--si-k-addr-ink); }
.t-kind.k-amount { --bg: var(--si-k-amount-bg); --ink: var(--si-k-amount-ink); }
.t-kind.k-other { --bg: var(--si-k-other-bg); --ink: var(--si-k-other-ink); }
.plain { background: transparent; box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ink) 28%, transparent); }
.x { display: grid; place-items: center; width: 16px; height: 16px; margin-right: -4px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: inherit; cursor: pointer; }
.x:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
</style>
