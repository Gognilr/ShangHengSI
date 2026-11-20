import { ElMessageBox } from 'element-plus'

/** 导出批注版的风险提示：只在本浏览器首次导出时弹确认框，之后靠菜单项下的说明（V321Q-36）。取消返回 false。 */
export async function confirmOnce(kind: 'annotated', message: string, title: string, button: string): Promise<boolean> {
  const key = `si.export-warned.${kind}`
  try { if (localStorage.getItem(key)) return true } catch { /* 存储不可用时每次都提示 */ }
  try { await ElMessageBox.confirm(message, title, { type: 'warning', confirmButtonText: button, cancelButtonText: '取消' }) } catch { return false }
  try { localStorage.setItem(key, '1') } catch { /* 忽略 */ }
  return true
}
