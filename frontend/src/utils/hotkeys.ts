import { onBeforeUnmount, onMounted, type Ref } from 'vue'

/**
 * 页面快捷键（V31-U2 3.6）。键名写法：'j'、'shift+j'、'ctrl+enter'、'?'、'escape'、'['。
 * 焦点在输入控件、下拉、可编辑区，或页面上有打开的对话框、抽屉时不触发（Esc 除外），
 * 避免打字或操作弹层时误触。组件卸载时自动解绑。
 */
export function useHotkeys(map: Record<string, (event: KeyboardEvent) => void>, options: { enabled?: Ref<boolean>; withinDialog?: boolean } = {}): void {
  const handler = (event: KeyboardEvent) => {
    if (event.isComposing || options.enabled?.value === false) return
    const key = keyName(event)
    const run = map[key]
    if (!run) return
    if (key !== 'escape' && (typingTarget(event.target) || (!options.withinDialog && overlayOpen()))) return
    event.preventDefault()
    run(event)
  }
  onMounted(() => window.addEventListener('keydown', handler))
  onBeforeUnmount(() => window.removeEventListener('keydown', handler))
}

function keyName(event: KeyboardEvent): string {
  const key = event.key.toLowerCase()
  // '?' 本身需要 Shift，不再拼前缀；其余按 ctrl / alt / shift 顺序拼接。
  if (key === '?') return '?'
  return [event.ctrlKey || event.metaKey ? 'ctrl' : '', event.altKey ? 'alt' : '', event.shiftKey ? 'shift' : '', key]
    .filter(Boolean).join('+')
}

function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return !!target.closest('input, textarea, select, [role="combobox"], [role="listbox"], .el-select, .el-input')
}

function overlayOpen(): boolean {
  return !!document.querySelector('.el-overlay:not([style*="display: none"]) .el-dialog, .el-overlay:not([style*="display: none"]) .el-drawer')
}
