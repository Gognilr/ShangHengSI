import { saveBlob } from '@/utils/save'
import { http } from './http'

async function download(path: string) {
  const { blob, filename } = await http.getDownload(path)
  return saveBlob(blob, filename)
}

function countHeader(headers: Headers, name: string): number {
  const value = Number(headers.get(name))
  return Number.isFinite(value) ? value : 0
}

export const reportApi = {
  run: (runId: string) => download(`/runs/${runId}/report`),
  local: (taskId: string) => download(`/tasks/${taskId}/report`),
  /** 标书初审报告（V34-B-07）：full 全部初审内容，clues 只含串标线索 */
  bid: (projectId: string, scope: 'full' | 'clues' = 'full') => download(`/bid/${projectId}/report?scope=${scope}`),
  async revised(runId: string, fileSeq: number) {
    const { blob, filename, headers } = await http.getDownload(`/runs/${runId}/revised?file_seq=${fileSeq}`)
    const saveResult = await saveBlob(blob, filename)
    return {
      saveResult,
      revised: countHeader(headers, 'X-SI-Revised'),
      commented: countHeader(headers, 'X-SI-Commented'),
    }
  },
  async annotated(runId: string, fileSeq: number) {
    const { blob, filename, headers } = await http.getDownload(`/runs/${runId}/annotated?file_seq=${fileSeq}`)
    const saveResult = await saveBlob(blob, filename)
    return {
      saveResult,
      total: countHeader(headers, 'X-SI-Total'),
      unanchored: countHeader(headers, 'X-SI-Unanchored'),
    }
  },
}
