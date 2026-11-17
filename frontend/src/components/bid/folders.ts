import type { BidFolder } from '@/api/bid'

/** count 为直接包含的项目数，total 含子文件夹（左侧树显示 total，与点开后的列表一致） */
export interface FolderNode { value: string; label: string; count: number; total: number; depth: number; children: FolderNode[] }

/** 平铺的文件夹按 parent_id 组成树（最多三级，V34-B-01a）；树选择与左侧文件夹树共用 */
export function folderOptions(rows: BidFolder[]): FolderNode[] {
  const byParent = new Map<string | null, BidFolder[]>()
  rows.forEach(r => byParent.set(r.parent_id, [...(byParent.get(r.parent_id) ?? []), r]))
  const build = (parent: string | null, depth: number): FolderNode[] =>
    (byParent.get(parent) ?? []).map(r => {
      const children = build(r.id, depth + 1)
      return { value: r.id, label: r.name, count: r.count, total: r.count + children.reduce((n, c) => n + c.total, 0), depth, children }
    })
  return build(null, 1)
}

/** 文件夹的路径，如“2026 年 / 设备” */
export function folderPath(rows: BidFolder[], id: string | null): string {
  const map = new Map(rows.map(r => [r.id, r]))
  const parts: string[] = []
  for (let cur = id ? map.get(id) : undefined; cur; cur = cur.parent_id ? map.get(cur.parent_id) : undefined) parts.unshift(cur.name)
  return parts.join(' / ')
}
