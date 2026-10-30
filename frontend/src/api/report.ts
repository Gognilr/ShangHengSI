import { saveBlob } from '@/utils/save'
import { http } from './http'

/** 下载并保存报告；返回 true 表示已写入工作文件夹（已提示，调用方不必再提示“已下载”）。 */
async function download(path: string): Promise<boolean> {
  const { blob, filename } = await http.getDownload(path)
  return saveBlob(blob, filename)
}

export const reportApi = {
  run: (runId: string) => download(`/runs/${runId}/report`),
  local: (taskId: string) => download(`/tasks/${taskId}/report`),
}
