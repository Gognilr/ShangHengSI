import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { useAppearanceStore, type FxLevel } from '@/stores/appearance'

/** 动效工具（V31-U1 3.1～3.3）。时长与 motion.css 的令牌一致。 */
const DURATIONS = { micro: 160, struct: 320, exit: 200 } as const

/** 当前解析后的特效级别。 */
export function useFx(): Ref<FxLevel> {
  const store = useAppearanceStore()
  return computed(() => store.fxResolved)
}

/** JS 驱动的动画取时长：特效关闭时为 0。 */
export function motionMs(layer: keyof typeof DURATIONS): number {
  return useAppearanceStore().fxResolved === 'off' ? 0 : DURATIONS[layer]
}

/**
 * 品牌时刻的循环是否此刻播放：等待中、特效为完整、页面可见（3.2 第 1 条）。
 * 精简与关闭时由组件显示静态替代。
 */
export function useLoop(active: Ref<boolean>): Ref<boolean> {
  const store = useAppearanceStore()
  const visible = ref(!document.hidden)
  const onVisibility = () => { visible.value = !document.hidden }
  onMounted(() => document.addEventListener('visibilitychange', onVisibility))
  onBeforeUnmount(() => document.removeEventListener('visibilitychange', onVisibility))
  return computed(() => active.value && visible.value && store.fxResolved === 'full')
}

/**
 * 品牌时刻开始时调用：前 1.5 秒（去掉前 200ms）平均帧间隔超过 34ms 时，
 * 自动档在本次打开期间降为精简（3.3）。返回停止函数。
 */
export function watchFrames(): () => void {
  const store = useAppearanceStore()
  let stopped = false
  let start = 0
  let last = 0
  let frames = 0
  let total = 0
  const tick = (now: number) => {
    if (stopped) return
    if (!start) start = now
    else if (now - start > 200) { total += now - last; frames += 1 }
    last = now
    if (now - start < 1500) { requestAnimationFrame(tick); return }
    if (frames > 0 && total / frames > 34) store.reportDroppedFrames()
  }
  requestAnimationFrame(tick)
  return () => { stopped = true }
}
