import { http } from './http'

export type Stance = 'sales' | 'purchase' | 'other'
export type Source = 'outgoing' | 'incoming'
export type Policy = 'ai' | 'local_only'
export type TaskStatus =
  | 'parsing'
  | 'parse_failed'
  | 'awaiting_confirm'
  | 'reviewing'
  | 'reviewed'
  | 'completed_local'

export interface TaskFile {
  id: string
  seq: number
  is_main: boolean
  original_name: string
  ext: string
  size_bytes: number
  doc_type: string | null
  doc_type_source: string | null
  page_count: number | null
  paragraph_count: number
  table_count: number
  has_revisions: boolean
  warnings: string[]
}

export interface LatestRun {
  id: string
  run_no: number
  status: string
}

export interface TaskDetail {
  id: string
  title: string
  status: TaskStatus
  error_message: string | null
  stance: Stance
  source: Source
  policy: Policy
  user_note: string | null
  created_at: string
  files: TaskFile[]
  latest_run: LatestRun | null
  queue_position: number | null
}

export interface TaskListItem {
  id: string
  title: string
  doc_type: string | null
  stance: Stance
  status: TaskStatus
  high_count: number
  created_at: string
}

export interface TaskPage {
  items: TaskListItem[]
  total: number
  page: number
  page_size: number
}

export interface CreateTaskInput {
  files: File[]
  mainIndex: number
  stance: Stance
  source: Source
  policy: Policy
  userNote: string
}

/** 上传限制（SPEC 6.2、17.6）；后端仍会再检查一遍。 */
export const MAX_FILES = 10
export const MAX_FILE_MB = 30
export const ALLOWED_EXTS = ['docx', 'pdf', 'xlsx', 'jpg', 'jpeg', 'png']
export const MAX_NOTE_LENGTH = 500

/** 前端预检查：返回第一条问题的中文说明，没有问题返回 null。 */
export function checkFiles(files: File[]): string | null {
  if (files.length === 0) return '请先选择要审核的文件'
  if (files.length > MAX_FILES) return `每个任务最多 ${MAX_FILES} 个文件`
  for (const f of files) {
    const ext = f.name.includes('.') ? f.name.split('.').pop()!.toLowerCase() : ''
    if (!ALLOWED_EXTS.includes(ext)) return `不支持的文件格式：${f.name}（支持 DOCX、PDF、XLSX、JPG、PNG）`
    if (f.size > MAX_FILE_MB * 1024 * 1024) return `文件超过 ${MAX_FILE_MB} MB：${f.name}`
    if (f.size === 0) return `文件为空：${f.name}`
  }
  return null
}

/** 重新审核时可修改的字段（PATCH /tasks/{id}，至少一项；userNote 为空字符串表示清除）。 */
export interface TaskPatch {
  stance?: Stance
  source?: Source
  user_note?: string
}

export const tasksApi = {
  create(input: CreateTaskInput) {
    const form = new FormData()
    for (const f of input.files) form.append('files', f, f.name)
    form.append('main_index', String(input.mainIndex))
    form.append('stance', input.stance)
    form.append('source', input.source)
    form.append('policy', input.policy)
    if (input.userNote.trim()) form.append('user_note', input.userNote.trim())
    return http.postForm<{ id: string }>('/tasks', form)
  },
  get: (id: string) => http.get<TaskDetail>(`/tasks/${id}`),
  list: (query: { page?: number; q?: string; from?: string; to?: string }) =>
    http.get<TaskPage>('/tasks', query),
  remove: (id: string) => http.delete<void>(`/tasks/${id}`),
  /** 重新审核：修改立场、来源、本次补充说明，任务回到待确认（SPEC 7.4）。 */
  patch: (id: string, body: TaskPatch) => http.patch<TaskDetail>(`/tasks/${id}`, body),
  setFileDocType: (taskId: string, fileId: string, docType: string) =>
    http.patch<TaskFile>(`/tasks/${taskId}/files/${fileId}`, { doc_type: docType }),
}
