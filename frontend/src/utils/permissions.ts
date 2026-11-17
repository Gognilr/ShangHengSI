// 权限点（V31-U5F-07）：与 backend/app/security/permissions.py 一致；界面按权限点显示菜单、分页与分区。
// 后端每个接口仍会再校验，这里只决定显示什么。
export type Perm =
  | 'use.review' | 'use.bid' | 'use.assistant' | 'use.meeting' | 'use.translate' | 'use.export'
  | 'content.requirements' | 'content.templates' | 'content.kb' | 'content.terms' | 'content.laws' | 'content.bid_categories'
  | 'redaction.names' | 'redaction.words' | 'redaction.parties'
  | 'sys.accounts' | 'sys.models' | 'sys.data' | 'sys.lawsync'

/** 设置页分页：perms 为空表示所有人可见；否则持有任一权限点即可见。顺序即导航顺序。 */
export const SETTING_TABS: { name: string; label: string; search?: string; perms: Perm[] }[] = [
  { name: 'personal', label: '个人', search: '个人（外观、修改密码、登录设备）', perms: [] },
  { name: 'users', label: '账号', perms: ['sys.accounts'] },
  { name: 'roles', label: '角色', search: '角色与权限', perms: ['sys.accounts'] },
  { name: 'model', label: '模型', perms: ['sys.models'] },
  { name: 'requirements', label: '审核要求', perms: ['content.requirements'] },
  { name: 'laws', label: '法规库', perms: ['content.laws', 'sys.lawsync'] },
  { name: 'templates', label: '公司范本', perms: ['content.templates'] },
  { name: 'knowledge', label: '知识库', perms: ['content.kb'] },
  { name: 'bid-categories', label: '标书分类', perms: ['content.bid_categories'] },
  { name: 'redaction', label: '脱敏', perms: ['redaction.names', 'redaction.words', 'redaction.parties', 'content.terms'] },
  { name: 'data', label: '数据', perms: ['sys.data'] },
]

/** 首页按使用权限依次取工作台、文件审核、标书初审、会议纪要；最后是人人可用的本地翻译。 */
export function homeFor(can: (...p: Perm[]) => boolean): string {
  return can('use.assistant') ? '/assistant' : can('use.review') ? '/review' : can('use.bid') ? '/bid' : can('use.meeting') ? '/meeting' : '/translate'
}
