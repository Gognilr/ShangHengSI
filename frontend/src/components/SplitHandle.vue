<script lang="ts">
/** 读取保存的宽度并夹到范围内；读不到或不是数字时用默认值。 */
export function readStoredWidth(key: string, min: number, max: number, fallback: number): number {
  let value = Number.NaN
  try { value = Number(localStorage.getItem(key)) } catch { /* 按默认值 */ }
  return Number.isFinite(value) && value > 0 ? Math.round(Math.min(max, Math.max(min, value))) : fallback
}
</script>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

// 通用竖向分隔条：拖动或 ←/→ 调整相邻栏宽度（像素），双击恢复默认；宽度写入本浏览器偏好（V3-A 〇.2）。
const props = withDefaults(defineProps<{ min: number; max: number; defaultValue: number; storageKey: string; label: string; disabled?: boolean; reverse?: boolean }>(), { disabled: false, reverse: false })
const model = defineModel<number>({ required: true })
const dragging = ref(false)
let startX = 0, startValue = 0

function clamp(value: number) { return Math.round(Math.min(props.max, Math.max(props.min, value))) }
function save(value: number) { try { localStorage.setItem(props.storageKey, String(value)) } catch { /* 存不了只影响下次默认宽度 */ } }
// v-model 的值要等父组件重新渲染后才更新，连续操作与保存都用这里记下的新值
let latest = model.value
watch(model, value => { if (value !== undefined) latest = value })
function set(value: number) { latest = clamp(value); model.value = latest; save(latest) }

function onMove(event: PointerEvent) { const delta = event.clientX - startX; latest = clamp(startValue + (props.reverse ? -delta : delta)); model.value = latest }
function onUp() {
  dragging.value = false; save(latest)
  window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp)
  document.body.style.removeProperty('cursor'); document.body.style.removeProperty('user-select')
}
function onDown(event: PointerEvent) {
  if (props.disabled || event.button !== 0) return
  event.preventDefault()
  dragging.value = true; startX = event.clientX; startValue = latest = model.value
  document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp)
}
function onKey(event: KeyboardEvent) {
  if (props.disabled || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return
  event.preventDefault()
  const step = (event.key === 'ArrowRight' ? 16 : -16) * (props.reverse ? -1 : 1)
  set(latest + step)
}
onBeforeUnmount(() => { if (dragging.value) onUp() })
</script>

<template>
  <div
    class="split-handle"
    :class="{ dragging, disabled }"
    role="separator"
    aria-orientation="vertical"
    :aria-label="label"
    :aria-valuenow="model"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :tabindex="disabled ? -1 : 0"
    @pointerdown="onDown"
    @keydown="onKey"
    @dblclick="!disabled && set(defaultValue)"
  ></div>
</template>

<style scoped>
.split-handle { position: relative; width: 8px; flex: none; cursor: col-resize; touch-action: none; }
.split-handle::after { content: ''; position: absolute; top: 0; bottom: 0; left: 3px; width: 2px; background: transparent; transition: background-color 150ms; }
.split-handle:hover::after, .split-handle.dragging::after, .split-handle:focus-visible::after { background: var(--el-color-primary); }
.split-handle:focus-visible { outline: none; }
.split-handle.disabled { cursor: default; pointer-events: none; }
</style>
