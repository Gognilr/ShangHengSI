import { http } from './http'

/** 普通用户模型条目只有显示信息与用途筛选后的 ID。 */
export interface AvailableModel { id: number; display_name: string; description: string | null; is_default: boolean; structured_ok: boolean }
export const modelsApi = { list: (purpose: 'review' | 'assist') => http.get<{ items: AvailableModel[] }>('/models', { purpose }) }
