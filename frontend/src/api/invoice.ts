import { http } from './http'

/** 发票管家（V36-I，设计第六节）：一人同时只有一批；原件与结果只在服务器内存盘，按时清除。 */
export type InvoiceBatchStatus = 'queued' | 'running' | 'done' | 'failed'

/** 清单预览的一行（完整的列在 Excel 中）；金额是两位小数的字符串，红字为负数 */
export interface InvoiceRow {
  seq: number
  kind: string
  code: string
  number: string
  date: string
  buyer_name: string
  buyer_tax: string
  seller_name: string
  seller_tax: string
  item: string
  amount: string | null
  tax: string | null
  total: string | null
  rates: string
  remark: string
  files: string[]
  source: string
  hints: string[]
}

export interface InvoiceBatch {
  id: string
  status: InvoiceBatchStatus
  progress: { done: number; total: number }
  expires_at: string
  error?: string
  rows?: InvoiceRow[]
  unrecognized?: { name: string; reason: string }[]
  summary?: { count: number; amount: string; tax: string; total: string; hinted: number }
}

export const invoiceApi = {
  upload: (files: File[], onProgress: (loaded: number, total: number) => void) => {
    const form = new FormData()
    for (const f of files) form.append('files', f, f.name)
    return http.postFormWithProgress<{ id: string }>('/invoice/batches', form, onProgress)
  },
  get: (id: string) => http.get<InvoiceBatch>(`/invoice/batches/${id}`),
  excel: (id: string) => http.getDownload(`/invoice/batches/${id}/excel`),
  clear: (id: string) => http.delete<void>(`/invoice/batches/${id}`),
}
