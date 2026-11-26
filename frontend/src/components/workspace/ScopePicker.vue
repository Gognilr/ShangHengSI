<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { assistantApi, type DeltaInfo } from '@/api/assistant'
import type { Stance } from '@/api/tasks'

/**
 * 审核范围（V35-D-03，diff.md 4.1）：主文件有上一稿时出现。“只审变化”= 变化的条款 + 上轮未解决的问题；
 * 字数来自外发预览（与实际发送一致），不是估算。满足条件时默认只审变化，改动超过六成时默认全文重审。
 */
const props = defineProps<{ sessionId: string; stance: Stance; fileIds: string[]; mainId: string; previousId: string; materialVersion: number | null; includePending?: boolean; compact?: boolean }>()
const scope = defineModel<'full' | 'delta'>({ required: true })
const ready = defineModel<boolean>('ready', { required: true })

const info = ref<DeltaInfo | null>(null)
const fullChars = ref<number | null>(null), deltaChars = ref<number | null>(null)
const error = ref('')
let touched = false, seq = 0
let version: number | null = null
async function load() {
  const current = ++seq
  ready.value = false
  error.value = ''
  info.value = null
  fullChars.value = null
  deltaChars.value = null
  const q = { stance: props.stance, file_ids: props.fileIds, include_pending: props.includePending }
  try {
    const [full, delta] = await Promise.all([
      assistantApi.outboundReview(props.sessionId, q),
      assistantApi.outboundReview(props.sessionId, { ...q, file_ids: [props.mainId], scope: 'delta', main_file_id: props.mainId }),
    ])
    if (current !== seq) return
    version = delta.material_version
    info.value = delta.delta ?? null
    fullChars.value = full.chars ?? null
    deltaChars.value = delta.chars ?? null
    if (!info.value?.available) scope.value = 'full'
    else if (!touched) scope.value = info.value.recommended ? 'delta' : 'full'
  } catch (e) {
    if (current === seq) {
      error.value = e instanceof ApiError ? e.message : '读取审核范围失败'
      if (scope.value !== 'full') scope.value = 'full'
    }
  } finally {
    if (current === seq) ready.value = true
  }
}
watch(() => [props.sessionId, props.mainId, props.previousId].join('|'), () => { touched = false })
watch(() => [props.sessionId, props.stance, props.mainId, props.previousId, props.fileIds.join(','), props.includePending].join('|'), () => void load(), { immediate: true })
watch(() => props.materialVersion, v => { if (ready.value && v !== null && v !== version) void load() })
function pick(value: 'full' | 'delta') { if (value === 'delta' && !info.value?.available) return; touched = true; scope.value = value }
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString('zh-CN')
const deltaText = computed(() => {
  const i = info.value
  if (!i?.available) return i?.reason ?? '正在读取…'
  if (i.identical) return `与第 ${i.prev_no} 稿正文一致，无需重审`
  return `变化的 ${i.changed_clauses} 条条款${i.priors_sent ? ` + 上轮未解决的 ${i.priors_sent} 个问题` : ''}，约发送 ${n(deltaChars.value)} 字`
})
</script>

<template>
  <div class="scope" :class="{ compact }" role="radiogroup" aria-label="审核范围">
    <span class="label">审核范围</span>
    <button type="button" role="radio" class="opt" :class="{ on: scope === 'delta', off: !info?.available }" :aria-checked="scope === 'delta'" :disabled="!info?.available" @click="pick('delta')">
      <b>只审变化<em v-if="info?.available && info.base_run_no" class="sub">对照第 {{ info.base_run_no }} 轮</em></b>
      <span>{{ deltaText }}</span>
    </button>
    <button type="button" role="radio" class="opt" :class="{ on: scope === 'full' }" :aria-checked="scope === 'full'" @click="pick('full')">
      <b>全文重审</b>
      <span>{{ fullChars === null ? '正在读取…' : `约发送 ${n(fullChars)} 字` }}</span>
    </button>
    <p v-if="info?.available && info.advice" class="hint warn">{{ info.advice }}</p>
    <p v-else-if="scope === 'delta' && info?.priors_total && info.priors_total > (info.priors_sent ?? 0)" class="hint">另有 {{ info.priors_total - (info.priors_sent ?? 0) }} 个上轮问题直接沿用上轮的处理状态。</p>
    <p v-if="error" class="hint warn">{{ error }}</p>
  </div>
</template>

<style scoped>
.scope { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.label { color: var(--si-ink-3); font: var(--si-font-label-sm); }
.opt { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%; padding: 8px 12px; border: 1px solid var(--si-hair); border-radius: 10px; background: var(--si-surface-1); color: var(--si-ink-2); text-align: left; cursor: pointer; transition: border-color var(--si-d2), background var(--si-d2); }
.opt:hover:not(:disabled) { border-color: var(--si-hair-strong); }
.opt:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
.opt.on { border-color: var(--si-brand-2); background: var(--si-blue-tint); color: var(--si-ink-1); }
.opt.off { cursor: default; opacity: .7; }
.opt b { display: flex; align-items: baseline; gap: 8px; font: var(--si-font-label); color: var(--si-ink-1); }
.opt span { font: var(--si-font-caption); color: var(--si-ink-3); }
.sub { font: var(--si-font-caption); font-style: normal; color: var(--si-ink-3); }
.hint { margin: 0; color: var(--si-ink-3); font: var(--si-font-caption); }
.warn { color: var(--si-warn-ink); }
.compact .opt { padding: 6px 10px; }
</style>
