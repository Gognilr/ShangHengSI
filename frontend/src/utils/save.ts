import { ElMessage } from 'element-plus'
import { saveInto } from '@/utils/workFolder'

type SaveHandle = { name: string; createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void>; abort(): Promise<void> }> }
type SaveWindow = Window & {
  showSaveFilePicker?: (options: { suggestedName: string; types?: { description: string; accept: Record<string, string[]> }[] }) => Promise<SaveHandle>
}

const TYPES: Record<string, { description: string; mime: string }> = {
  docx: { description: 'Word 文档', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  xlsx: { description: 'Excel 工作簿', mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
}

/**
 * 系统“另存为”（V31-U3 1.5，V31Q-82）。返回 true 表示已保存或用户取消（都已提示），
 * false 表示不支持、点击的激活已过期或写入失败，调用方改为普通下载。
 */
async function saveAs(blob: Blob, filename: string): Promise<boolean> {
  const picker = (window as SaveWindow).showSaveFilePicker
  const active = (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation?.isActive
  if (typeof picker !== 'function' || !window.isSecureContext || !active) return false
  const ext = filename.slice(filename.lastIndexOf('.') + 1).toLowerCase()
  const type = TYPES[ext]
  let handle: SaveHandle
  try {
    handle = await picker.call(window, { suggestedName: filename, types: type ? [{ description: type.description, accept: { [type.mime]: [`.${ext}`] } }] : undefined })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') { ElMessage.info('已取消保存'); return true }
    return false
  }
  let writable: Awaited<ReturnType<SaveHandle['createWritable']>>
  try { writable = await handle.createWritable() } catch { return false }
  try { await writable.write(blob); await writable.close() }
  catch { try { await writable.abort() } catch { /* 已失败 */ } return false }
  ElMessage.success(`已保存：${handle.name}`)
  return true
}

/**
 * 导出文件的统一保存入口（V3-A 7.2、V31-U3 1.5）：有工作文件夹且已授权时写入该文件夹（同名加序号，不覆盖）；
 * 否则弹出系统“另存为”；不支持、激活已过期或写入失败时按普通下载。
 * 返回 true 表示已写入文件夹、已另存或用户取消（已提示），调用方不必再提示“已下载”。
 */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  try {
    const saved = await saveInto(blob, filename)
    if (saved) { ElMessage.success(`已保存到工作文件夹：${saved}`); return true }
  } catch (e) {
    ElMessage.error(`写入工作文件夹失败（${e instanceof Error && e.message ? e.message : '未知原因'}），已改为普通下载`)
    return download(blob, filename)
  }
  if (await saveAs(blob, filename)) return true
  return download(blob, filename)
}

function download(blob: Blob, filename: string): false {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 给浏览器处理下载点击的时间，再释放临时 URL。
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return false
}
