import { onBeforeUnmount, ref, watch, type WatchSource } from 'vue'

/** 来源值超过 ms 毫秒没有变化时为 true（V321-E-12：长等待提示）。 */
export function useStale(source: WatchSource, ms = 10000) {
  const stale = ref(false)
  let timer: ReturnType<typeof setTimeout> | undefined
  watch(source, () => { stale.value = false; clearTimeout(timer); timer = setTimeout(() => { stale.value = true }, ms) }, { immediate: true })
  onBeforeUnmount(() => clearTimeout(timer))
  return stale
}
