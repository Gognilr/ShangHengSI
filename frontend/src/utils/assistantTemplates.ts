import type { Session, Template } from '@/api/assistant'

/** 各模板可用文件的条件与数量（SPEC 13A 第 2、7 条），输入区与“选了模板就引导上传”共用。 */
export const TEMPLATE_LIMITS: Record<Template, [number, number]> = {
  translate: [1, 1], summary: [1, 3], reply: [0, 3], crosscheck: [2, 6], compare: [2, 6], minutes: [1, 3],
}

export function usableFiles(session: Session, template: Template | null) {
  return session.files.filter(f => f.confirmed_at && (template !== 'minutes' || f.media?.stage === 'done'))
}

/** 选了模板但会话里可用文件不够、也没有正在处理的文件时，应直接引导加入文件。 */
export function needsUpload(session: Session, template: Template | null) {
  if (!template) return false
  const pending = session.files.some(f => !f.confirmed_at && (template !== 'minutes' || !!f.media))
  return !pending && usableFiles(session, template).length < TEMPLATE_LIMITS[template][0]
}
