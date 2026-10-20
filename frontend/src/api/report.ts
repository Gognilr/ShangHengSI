import { http } from './http'

async function download(path: string): Promise<void> {
  const { blob, filename } = await http.getDownload(path)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 给浏览器处理下载点击的时间，再释放临时 URL。
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const reportApi = {
  run: (runId: string) => download(`/runs/${runId}/report`),
  local: (taskId: string) => download(`/tasks/${taskId}/report`),
}
