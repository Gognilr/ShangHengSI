<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { currentToast, dismissToast } from '@/utils/toast'
import { useFx } from '@/utils/motion'

/** 可撤销提示的显示层，挂在 App.vue 一次。鼠标悬停或焦点在其中时暂停计时。 */
const fx = useFx()
const paused = ref(false)
let timer: number | undefined
let startedAt = 0
let remaining = 0

function clearTimer() { if (timer !== undefined) { window.clearTimeout(timer); timer = undefined } }
function run() {
  clearTimer()
  startedAt = Date.now()
  timer = window.setTimeout(dismissToast, remaining)
}
function pause() {
  if (paused.value || !currentToast.value) return
  paused.value = true
  remaining -= Date.now() - startedAt
  clearTimer()
}
function resume() {
  if (!paused.value || !currentToast.value) return
  paused.value = false
  run()
}
function onAction() {
  const action = currentToast.value?.action
  dismissToast()
  action?.onClick()
}

watch(currentToast, (value) => {
  paused.value = false
  if (!value) { clearTimer(); return }
  remaining = value.duration
  run()
})
onBeforeUnmount(clearTimer)
</script>

<template>
  <Transition name="si-rise">
    <div v-if="currentToast" :key="currentToast.id" class="si-toast" role="status" aria-live="polite" @mouseenter="pause" @mouseleave="resume" @focusin="pause" @focusout="resume">
      <span class="text">{{ currentToast.text }}</span>
      <button v-if="currentToast.action" type="button" class="action" @click="onAction">{{ currentToast.action.label }}</button>
      <span v-if="fx !== 'off'" class="bar" :style="{ animationDuration: `${currentToast.duration}ms`, animationPlayState: paused ? 'paused' : 'running' }" aria-hidden="true" />
    </div>
  </Transition>
</template>

<style scoped>
.si-toast { position: fixed; right: 24px; bottom: 24px; z-index: 4000; display: flex; align-items: center; gap: 16px; max-width: 480px; padding: 10px 18px; overflow: hidden; border-radius: 999px; background: var(--si-toast-bg); color: var(--si-toast-ink); box-shadow: var(--si-shadow-3); font: var(--si-font-label); }
.text { min-width: 0; overflow-wrap: anywhere; }
.action { flex: none; padding: 0; border: 0; background: transparent; color: inherit; font: var(--si-font-label); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.action:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 3px; border-radius: 4px; }
.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: currentColor; opacity: .45; transform-origin: left; animation: si-toast-count linear forwards; }
@keyframes si-toast-count { from { transform: scaleX(1); } to { transform: scaleX(0); } }
</style>
