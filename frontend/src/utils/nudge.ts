import { useAppearanceStore } from '@/stores/appearance'

/**
 * 缺必选项时把用户带过去（V33-C-01，frontend.md 第七节）：滚到该项、聚焦其中第一个可操作的控件、
 * 主色框闪一次（motion.css 的 .si-nudge；特效关闭时不闪，只显示约 1 秒的静态框）。
 */
const FOCUSABLE = '[role="radio"][tabindex="0"], input:not([disabled]):not([type="hidden"]), button:not([disabled]), [tabindex="0"]'
const timers = new WeakMap<HTMLElement, number>()

export function nudgeField(el: Element | null | undefined): boolean {
  if (!(el instanceof HTMLElement)) return false
  const off = useAppearanceStore().fxResolved === 'off'
  el.scrollIntoView({ block: 'nearest', behavior: off ? 'auto' : 'smooth' })
  el.querySelector<HTMLElement>(FOCUSABLE)?.focus({ preventScroll: true })
  window.clearTimeout(timers.get(el))
  el.classList.remove('si-nudge')
  void el.offsetWidth // 重新触发动画
  el.classList.add('si-nudge')
  timers.set(el, window.setTimeout(() => el.classList.remove('si-nudge'), 1200))
  return true
}
