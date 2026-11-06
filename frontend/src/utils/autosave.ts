import { onBeforeUnmount, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ApiError } from '@/api/http'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

/**
 * 设置页自动保存（V31-U5F-06）：开关、下拉改动立即保存（schedule(0)），文字输入停顿 1 秒后保存。
 * save 只在内容与服务端不同时才需要发请求，由调用方自己判断；失败时保留 status=error，可 retry()。
 * 离开页面时若还有等待中的保存，立即发出。
 */
export function useAutoSave(save: () => Promise<void>, idleMs = 1000) {
  const status = ref<SaveStatus>('idle')
  const message = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  let running = false
  let again = false
  let fadeTimer: ReturnType<typeof setTimeout> | null = null

  async function run() {
    timer = null
    if (running) { again = true; return }
    running = true
    status.value = 'saving'
    message.value = ''
    try {
      await save()
      status.value = 'saved'
      if (fadeTimer) clearTimeout(fadeTimer)
      fadeTimer = setTimeout(() => { if (status.value === 'saved') status.value = 'idle' }, 4000)
    } catch (e) {
      status.value = 'error'
      message.value = e instanceof ApiError ? e.message : '请稍后重试'
    } finally {
      running = false
      if (again) { again = false; void run() }
    }
  }
  function schedule(delay = idleMs) {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { void run() }, delay)
  }
  function flush() { if (timer) { clearTimeout(timer); void run() } }
  onBeforeRouteLeave(() => { flush() })
  onBeforeUnmount(() => { flush(); if (fadeTimer) clearTimeout(fadeTimer) })
  return { status, message, schedule, retry: () => run(), flush }
}
