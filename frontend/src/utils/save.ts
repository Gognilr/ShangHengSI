import { ElMessage } from 'element-plus'
import { saveInto } from '@/utils/workFolder'

/**
 * 导出文件的统一保存入口（V3-A 7.2）：有工作文件夹且已授权时写入该文件夹（同名加序号，不覆盖）；
 * 没有、未授权或写入失败时按普通下载。返回 true 表示已写入工作文件夹（并已提示），调用方不必再提示“已下载”。
 */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  try {
    const saved = await saveInto(blob, filename)
    if (saved) { ElMessage.success(`已保存到工作文件夹：${saved}`); return true }
  } catch (e) {
    ElMessage.error(`写入工作文件夹失败（${e instanceof Error && e.message ? e.message : '未知原因'}），已改为普通下载`)
  }
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
