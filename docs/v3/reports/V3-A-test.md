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

## V3-A-03：智能助手两栏、对话与输入框（Opus 5.5，2026-10-30）

- `Assistant.vue` 改为两栏，删除 `SessionList.vue`。新建、改名、删除、发送、生成结束后调用 `sessions.refresh()`，不再每秒刷新列表；在侧栏改名后同步顶部标题。
- 提前新建 `components/EmptyState.vue`（按 4.2 规格，含 `compact`），V3-A-04 直接复用。
- `TemplatePicker` 非卡片模式改为下拉（“不用模板”可清空），放在输入框下方一行；V3-B 的模型选择放同一行。
- `npm --prefix frontend run build`：通过。
- 本机浏览器（v2f 测试环境，查完已删测试会话并退出）：空状态；新建会话；Shift+Enter 换行，Enter 打开“确认发送内容”，确认后用户气泡靠右；失败回复的“重试”始终显示；1280×800、1440×900 布局正常，无控制台报错。
- 未在浏览器验证：生成中的“停止”按钮、“正在生成…”、悬停显示操作、“回到底部”。v2f 的模拟模型对助手请求直接返回失败，这几项留到 V3-A-08 用能正常流式回复的模拟服务检查。

## V3-A-04～07 实施与 V3-A-08 集中测试（2026-10-30，Sonnet 5.5）

**实施要点**：04 审核结果可拖动分栏（`si.review.leftWidth`）、任务状态标签、`EmptyState` / `TableSkeleton` / `RowMore`、短提示右下角（App.vue 的 `el-config-provider` 设置 `placement`，全部 `ElMessage` 调用无需改动）、表格表头固定 / 行尾“⋯” / 长名称省略 / 首次骨架；05 `SettingSection`、`UnsavedBar`、`utils/unsaved.ts`（统一保存 / 放弃、离开与关闭页面提醒；数据页的备份、数据保留、法规库自动更新与审核要求的公司说明进入未保存状态）；06 `POST /search` 与全局搜索；07 清单与图标、运维说明 5.1、工作文件夹（`workFolder.ts`、`save.ts`、两个组件）。新增 V3Q-09。

**自动检查**：`npm --prefix frontend run build` 通过；`tests/test_search.py` 2 个用例通过；`test_frontend_style.py`、`test_scripts.py` 通过（共 4 个）。**整仓全量未跑完**：约 25 分钟只走到 6% 即中止（本批后端只新增 search 路由，其余为前端、文档与 Caddyfile 一行），请 V3-A-09 或 V3-Z 在需要时整仓补跑。

**浏览器检查**（本机 5179 → 当前代码后端，虚构数据，截图在 `V3-A/`）：

| 项 | 结果 |
|-|-|
| 强制改密：主区域只显示提示，改密对话框不可关 | 通过 |
| 设置 - 数据：分节、改动后出现底部提示栏；点其他分类弹“有未保存的修改”，“留在此页”留下；保存后提示栏消失并提示“已保存”，提示在右下角 | 通过 |
| 设置 - 审核要求 1280 宽：节改为上下排列 | 通过 |
| 全局搜索：快捷键打开（用脚本派发 Ctrl+K，浏览器面板的按键通道吞掉 Ctrl 组合键）、页面过滤、无结果空状态 | 通过 |
| 审核历史空状态；新建审核页的“选择工作文件夹”入口 | 通过 |

**未能在本次检查的项**（留到 V3-Z 在测试虚拟机上演练或用户使用时确认）：工作文件夹选择 / 列出 / 写入（`showDirectoryPicker` 的系统对话框无法自动化，只做了代码走查：选定不上传、勾选加入才读取、同名加序号不覆盖、失败退回下载）；审核结果拖动分栏与表头固定（本地库没有已完成的审核任务）；1920 宽；换用户登录确认搜索范围（由 `test_search.py` 覆盖）；Edge 实际“安装为应用”（清单与图标已就绪，需 HTTPS 与已导入根证书）。

**检查中发现并已改**：数据页“备份记录”表放在右侧窄列里被截断，改为独立的 stacked 节。

## V3-A-09：审核与合并规格（Opus 5.5，2026-10-30）

**审阅结论**：V3-A-04～08 符合设计，通过。核对 PLAN 第四节安全不变量：搜索服务端按 owner 过滤、搜索词放请求体且不记日志；工作文件夹选定不上传、只读第一层支持格式、同名加序号不覆盖、失败退回下载；偏好存储读写均有 try/catch，不存业务内容。

**审核中修改**：审核历史状态列“已审核”“本地校验完成”原为绿色标签，容易读成“审核通过”（frontend.md 第五节不用暗示安全的颜色），改为 `primary`（`utils/labels.ts`）。前端构建通过。

**已知限制（不改）**：导出写入工作文件夹前若需重新授权，`requestPermission` 发生在下载请求之后，浏览器可能因用户点击已过时而拒绝，此时按设计退回普通下载。

**合并**：SPEC（变更记录、1.6、3、6.8、新增 6.9、13A 第 2 条、19.3 `POST /search`、20.4、23.2 T-61）；frontend.md（版本说明、第一、二、四、五、六、九、十节）；CLAUDE.md 菜单表述。AGENTS.md 没有菜单表述，不改。版本号仍为 V2.1。

**未做**：整仓全量（V3-A-08 中止）留到 V3-Z 一并跑；本批后端只新增 search 路由，其用例已通过。
