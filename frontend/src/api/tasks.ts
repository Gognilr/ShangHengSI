import { http } from './http'

export type Stance = 'sales' | 'purchase' | 'other'
export type Source = 'outgoing' | 'incoming'
export type Policy = 'ai' | 'local_only'
export type TaskStatus =
  | 'draft'
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
  annotatable: boolean
  warnings: string[]
  /** V31-S：解析中 / 完成 / 失败（由服务器按文件派生）。 */
  parse_state: 'parsing' | 'done' | 'failed'
  /** 解析失败的原因，不含原文。 */
  parse_error: string | null
  /** 原件按保留期限删除的时间；未删除为 null。 */
  original_deleted_at?: string | null
}

export interface LatestRun {
  id: string
  run_no: number
  status: string
}

export interface TaskDetail {
  template_suggestion: { id: string; name: string } | null
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
  original_expires_at: string | null
  task_expires_at: string | null
  /** 本任务有排队或进行中的解析（V31-S）。 */
  busy: boolean
}

export interface TaskListItem {
  id: string
  title: string
  doc_type: string | null
  stance: Stance
  status: TaskStatus
  high_count: number
  created_at: string
  original_expires_at: string | null
  task_expires_at: string | null
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
  allowDuplicate?: boolean
  /** 草稿任务（拖入即解析，V31-S 第四节）；草稿时主文件固定为第一个。 */
  draft?: boolean
}

export interface ProceedInput {
  stance: Stance
  source: Source
  policy: Policy
  user_note: string | null
}

/** 草稿与文件接口的错误提示（按错误码，其余用接口返回的 message）。 */
export const TASK_ERROR_MESSAGES: Record<string, string> = {
  DUPLICATE_IN_TASK: '该文件已在本任务中',
  LAST_FILE: '这是任务中的最后一个文件，请删除整个任务',
  FILES_NOT_READY: '文件还没有解析完成',
  TOO_MANY_FILES: '每个任务最多 10 个文件',
  TASK_STATE_CONFLICT: '任务状态已变化，请刷新后再试',
}

export interface DuplicateFile {
  task_id: string
  title: string
  created_at: string
}

/** 上传限制（SPEC 6.2、17.6）；后端仍会再检查一遍。 */
export const MAX_FILES = 10
export const MAX_FILE_MB = 30
export const ALLOWED_EXTS = ['docx', 'doc', 'wps', 'rtf', 'pdf', 'xlsx', 'xls', 'et', 'jpg', 'jpeg', 'png']
export const MAX_NOTE_LENGTH = 500

/** 前端预检查：返回第一条问题的中文说明，没有问题返回 null。 */
export function checkFiles(files: File[]): string | null {
  if (files.length === 0) return '请先选择要审核的文件'
  if (files.length > MAX_FILES) return `每个任务最多 ${MAX_FILES} 个文件`
  for (const f of files) {
    const ext = f.name.includes('.') ? f.name.split('.').pop()!.toLowerCase() : ''
    if (!ALLOWED_EXTS.includes(ext)) return `不支持的文件格式：${f.name}（支持 DOCX、DOC、WPS、RTF、PDF、XLSX、XLS、ET、JPG、PNG）`
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
    form.append('allow_duplicate', String(input.allowDuplicate ?? false))
    if (input.draft) form.append('draft', 'true')
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
  /** 设为主文件：标题随之改变，后台重算（V31-S 4.3）。 */
  setMain: (taskId: string, fileId: string) =>
    http.patch<TaskFile>(`/tasks/${taskId}/files/${fileId}`, { is_main: true }),
  /** 向草稿或从未提交审核的任务追加文件并入队解析。 */
  addFiles(taskId: string, files: File[]) {
    const form = new FormData()
    for (const f of files) form.append('files', f, f.name)
    return http.postForm<TaskDetail>(`/tasks/${taskId}/files`, form)
  },
  /** keepalive：关闭标签页时仍发出（延迟删除到期前离开页面）。 */
  removeFile: (taskId: string, fileId: string, opts: { keepalive?: boolean } = {}) =>
    http.delete<TaskDetail>(`/tasks/${taskId}/files/${fileId}`, opts),
  /** 草稿进入第二步（ai）或完成本地校验（local_only）。 */
  proceed: (taskId: string, body: ProceedInput) => http.post<TaskDetail>(`/tasks/${taskId}/proceed`, body),
}
