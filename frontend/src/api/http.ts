/**
 * 请求封装（SPEC 19.1）：前缀 /api/v1；修改类请求自动带 X-CSRF-Token；
 * 401 时清除登录状态并跳转登录页；错误统一为 ApiError（code / message 来自后端）。
 */

export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

interface Hooks {
  /** 收到 401（会话失效或未登录）。 */
  onUnauthorized: () => void
  /** 收到 403 PASSWORD_CHANGE_REQUIRED（须先修改密码）。 */
  onPasswordChangeRequired: () => void
}

let csrfToken = ''
let hooks: Hooks = { onUnauthorized: () => {}, onPasswordChangeRequired: () => {} }

export function setCsrfToken(token: string): void {
  csrfToken = token
}

export function setHttpHooks(next: Hooks): void {
  hooks = next
}

interface RequestOptions {
  body?: unknown
  /** multipart 表单；浏览器自动生成 Content-Type 和分隔符，不能手动设置。 */
  form?: FormData
  query?: Record<string, string | number | undefined>
  /** 为 true 时 401 不触发全局跳转（用于登录请求和启动时探测登录状态）。 */
  silent401?: boolean
}

async function request<T>(method: Method, path: string, opts: RequestOptions = {}): Promise<T> {
  let url = API_PREFIX + path
  if (opts.query) {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined && v !== '') params.set(k, String(v))
    }
    const qs = params.toString()
    if (qs) url += '?' + qs
  }

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined && !opts.form) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken

  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务器，请检查网络后重试')
  }

  if (res.ok) {
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  let code = 'HTTP_ERROR'
  let message = `请求失败（${res.status}）`
  try {
    const data = (await res.json()) as { code?: string; message?: string }
    if (data.code) code = data.code
    if (data.message) message = data.message
  } catch {
    // 响应不是 JSON（如网关错误页），保留默认说明
  }

  if (res.status === 401 && !opts.silent401) hooks.onUnauthorized()
  if (res.status === 403 && code === 'PASSWORD_CHANGE_REQUIRED') hooks.onPasswordChangeRequired()
  throw new ApiError(res.status, code, message)
}

export const http = {
  get: <T>(path: string, query?: RequestOptions['query'], opts?: Pick<RequestOptions, 'silent401'>) =>
    request<T>('GET', path, { query, ...opts }),
  post: <T>(path: string, body?: unknown, opts?: Pick<RequestOptions, 'silent401'>) =>
    request<T>('POST', path, { body, ...opts }),
  postForm: <T>(path: string, form: FormData) => request<T>('POST', path, { form }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
