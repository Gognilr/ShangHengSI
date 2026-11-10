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
