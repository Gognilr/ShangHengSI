// 权限点（V31-U5F-07）：与 backend/app/security/permissions.py 一致；界面按权限点显示菜单、分页与分区。
// 后端每个接口仍会再校验，这里只决定显示什么。
export type Perm =
  | 'use.review' | 'use.assistant' | 'use.meeting' | 'use.translate' | 'use.export'
  | 'content.requirements' | 'content.templates' | 'content.kb' | 'content.terms' | 'content.laws'
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
  { name: 'redaction', label: '脱敏', perms: ['redaction.names', 'redaction.words', 'redaction.parties', 'content.terms'] },
  { name: 'data', label: '数据', perms: ['sys.data'] },
]
