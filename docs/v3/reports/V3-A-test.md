# V3-A 验证记录

## V3-A-01：Lucide 图标（2026-10-30）

- 经用户同意下载 npm 依赖。通过 npm 官方注册表（`npm view lucide-vue-next version time --json --registry=https://registry.npmjs.org`）核对：最新版本为 **1.0.0**，发布时间 **2026-03-23T13:45:54.576Z**，截至实施日已满两周；package.json 精确锁定，package-lock.json 同步提交。该包已弃用，npm 建议改用 `@lucide/vue`；本次遵循任务卡指定的包名，不自行迁移。
- 替换 11 个 Vue 文件的显式图标引用；图标为 16px、线宽 1.75、继承文字色，按钮使用 icon 插槽且文字间距 6px。输入框改用 prefix 插槽以传入尺寸与线宽。Element Plus 内部自带图标保持原样。

| 原图标 | Lucide 图标 |
|-|-|
| Setting | Settings |
| Search | Search |
| Plus | Plus |
| Download | Download |
| Refresh | RotateCw |
| Delete | Trash2 |
| CopyDocument | Copy |
| ChatDotRound | MessageSquare |
| UploadFilled | Upload |
| Lock | Lock |
| User | User |
| Cpu | Cpu |
| List | List |
| Coin | Database |
| Check | Check |
| Close | X |

验证：

- `npm --prefix frontend ci --include=dev`：通过，安装 114 个包。首次因两个既有 Vite 进程占用原生绑定而失败；临时停止后重试通过，已恢复原 localhost 开发端口 5173、5175。
- `npm --prefix frontend run build`：通过（vue-tsc + Vite）；有分块大于 500 kB 的构建提示。
- 源码检查：`frontend/src` 已无 `@element-plus/icons-vue`；差异仅涉及图标引用、用法与间距，未新增颜色、HTML 注入、控制台输出或禁用措辞；`git diff --check` 通过。
- 按 V3 共同约定，本任务不运行后端、样式或整仓测试；浏览器、各尺寸与状态、键盘焦点和截图检查留到 V3-A-08，本次未进行视觉验收。
- 无新增 V3Q；没有调用真实模型。批次集中测试与 Opus 审核尚未完成。按首个前端任务约定，完成后停下交 Opus 重点评审。

### V3-A-01 评审返工（Opus 5.5，2026-10-30）

- 包改为 `@lucide/vue` **1.57.0**（npm 发布 2026-10-14，满两周的最新版）。`lucide-vue-next` 自 Lucide 1.0 起改名并弃用，1.0.0 为旧名最后一版；任务卡写错包名，已同步改 V3-A.md。导入名与属性不变。
- Element Plus 的 `.el-icon svg{width:1em;height:1em}` 盖掉了 `:size="16"`，按钮内约 14px、设置导航 18px；在 common.css 加 `.el-icon > svg.lucide` 定为 16px。
- 上传框 44px 底块中的图标改为 24px（不包 el-icon）。
- 删除四处 `:deep(.el-button .el-icon + span)`，与 Element Plus 默认 6px 重复。
- `npm --prefix frontend run build`：通过。浏览器尺寸核对留到 V3-A-08。

## V3-A-02：侧栏主布局与首页（Opus 5.5，2026-10-30）

- 新建 `SplitHandle.vue`（附 `readStoredWidth`）、`SidebarSessions.vue`、`stores/sessions.ts`；重写 `MainLayout.vue`；`SiLogo` 加 `markOnly`；首页改 `/assistant`（`main.ts` 中改密后的跳转也改用 `HOME_PATH`，超出原文件范围一行）；审核结果、智能助手根元素改 `height: 100%`。
- `common.css`：`.page` 加左右 32px 留白。侧栏展开后主区域在 1280 宽下只有 1048px，原来内容贴边。
- 近期会话按接口每页 30 条（V3Q-08）。全局搜索按钮已放好，对话框与 Ctrl K 由 V3-A-06 接上。
- `npm --prefix frontend run build`：通过。
- 本机浏览器（v2f 测试环境，临时管理员 ui-check，查完已删测试会话并退出）：强制改密时主区域只显示提示；侧栏新建会话后进入会话并出现在近期会话；收起 / 展开与偏好写入正确；分隔条方向键 16px、夹在 200～320、双击回 232、拖动写入正确；1280×800 与 1440×900 下智能审核、设置页布局正常，无控制台报错。修正一处：分隔条原先保存的是更新前的旧值。
- 交给 V3-A-04：1280 宽、侧栏展开时，账号管理表的操作列要在表格内横向滚动，按 4.4 表格细节处理。
