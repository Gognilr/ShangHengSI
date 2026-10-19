import { http } from './http'

export interface PreviewNode {
  loc: string
  kind: string
  part: string
  /** 解密后的原文；保留期清理后为 null。 */
  text: string | null
  clause_no: string | null
  page: number | null
  /** [x0, top, x1, bottom]；PDF 为 PDF 点，图片为原图像素，原点在左上角。 */
  bbox: number[] | null
  sheet: string | null
  cell_ref: string | null
  ocr_conf: number | null
  parent_loc: string | null
}

export interface PreviewNodes {
  text_available: boolean
  items: PreviewNode[]
}

export const previewApi = {
  nodes: (taskId: string, fileId: string) =>
    http.get<PreviewNodes>(`/tasks/${taskId}/files/${fileId}/nodes`),
  /** 原件字节（仅 PDF、JPG、PNG）；先取 Blob，不把地址直接交给 pdfjs 或 img。 */
  raw: (taskId: string, fileId: string) => http.getBlob(`/tasks/${taskId}/files/${fileId}/raw`),
}
