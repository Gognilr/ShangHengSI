import { http } from './http'

export interface LocalFinding {
  id: number
  check_code: string
  severity: string
  locs: string[]
  file_id: string | null
  file_name: string
  location: string
  detail: string
}

export const localFindingsApi = {
  list: (taskId: string) => http.get<{ items: LocalFinding[] }>(`/tasks/${taskId}/local-findings`),
}
