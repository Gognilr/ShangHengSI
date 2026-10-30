# V3-A 界面与安装

2026-10-30 · Opus 5.5 细化（V3-00）· **用户已确认（2026-10-30）**

范围见 [PLAN.md](../PLAN.md) 第二、三节；执行规则见 [TASKS-V3.md](../TASKS-V3.md) 第一节。本批不改后端业务逻辑，只新增一个搜索接口。本文件与 docs/design/frontend.md 冲突处，在本批内以本文件为准，V3-A-09 合并。

## 〇、共同约定

1. **只改外观与交互，不改流程**：发送前确认（V2Q-127）、脱敏确认、疑似遗漏处理、导出、删除确认全部保留；接口形状不变（除新增搜索）。
2. **偏好存本浏览器**：侧栏收起状态与宽度、审核结果左栏宽度写 `localStorage`，读写一律 `try/catch`，读不到按默认值；不存任何业务内容、文件名、路径。键名统一前缀 `si.`：`si.sidebar.collapsed`、`si.sidebar.width`、`si.review.leftWidth`。工作文件夹的授权句柄按第七节存在本浏览器的 IndexedDB。
3. **图标**：本批起只用 `@lucide/vue`（V3-A-01 起）。Element Plus 组件内部自带的图标（下拉箭头、关闭等）不处理。
4. **颜色**：十六进制颜色仍只写在 theme.css；唯一例外是 `public/manifest.webmanifest` 的 `theme_color`、`background_color`（浏览器要求写在清单中，取 theme.css 中品牌主色与页面底色的值）。
5. **测试**：前端任务只跑 `npm --prefix frontend run build`；后端只有搜索接口加用例。浏览器检查集中在 V3-A-08。
6. **尺寸**：以 1280×800、1440×900、1920×1080 三档检查；1280 宽、侧栏展开时审核结果右栏不足以阅读，由用户收起侧栏，不自动收起。

## 一、Lucide 图标（V3-A-01，GPT-6.1 Sol，medium）

* `frontend/package.json` 增加 `@lucide/vue`，**精确版本**，取实施当天 npm 上发布满两周的最新版本，写进任务报告；`npm ci` 后提交 `package-lock.json`。
* 把 `frontend/src` 中全部 `@element-plus/icons-vue` 的引用（约 11 处）换成 Lucide 对应图标。用法：`<Settings :size="16" :stroke-width="1.75" />`，颜色继承文字色（`currentColor`），不另写颜色；放进 `el-button` 时用其 `icon` 插槽或在文字前，图标与文字间距 6px。Element Plus 的 `.el-icon svg` 按 1em 取宽高，由 `styles/common.css` 一条规则把 `el-icon` 内的 Lucide 图标定为 16px；上传框中央图标例外，24px。
* 对应关系由实施者按图形含义选择（如 Setting → `Settings`、Search → `Search`、Plus → `Plus`、Download → `Download`、CopyDocument → `Copy`、Refresh → `RotateCw`、Upload → `Upload`、Document → `FileText`、ChatDotRound → `MessageSquare`），在报告中列一张表。
* 完成后 `frontend/src` 中不再出现 `@element-plus/icons-vue`。

**文件范围**：`frontend/package.json`、`frontend/package-lock.json`、`frontend/src/**/*.vue`（只改图标引用与用法）。

## 二、侧栏主布局与首页（V3-A-02，GPT-6.1 Sol，high）

### 2.1 结构（`layouts/MainLayout.vue` 重写）

```
┌────────────┬──────────────────────────────────────┐
│ 商衡SI 标识 │ 管理员提醒条（有提醒时）                │
│ [+ 新建会话]│                                      │
│ 智能助手    │                                      │
│ 智能审核    │            页面（router-view）         │
│ 审核历史    │                                      │
│ 搜索  Ctrl K│                                      │
│ ─ 近期会话 ─│                                      │
│ 会话标题…   │                                      │
│ 查看更多    │                                      │
│            │                                      │
│ 设置（管理员）│                                      │
│ 用户 ▾  [«] │                                      │
└────────────┴──────────────────────────────────────┘
```

* 整体 `display: grid; grid-template-columns: auto 1fr; height: 100vh`。侧栏默认宽 232px，**可拖宽**：右边缘放 `SplitHandle`（第四节），范围 200～320px，双击恢复 232px，宽度写 `si.sidebar.width`；收起为 64px（收起时不可拖）。白底、右边框 1px `--el-border-color-light`；内部纵向 flex，近期会话区自己滚动。`SplitHandle` 在本任务中先建（4.1 的规格），V3-A-04 直接复用。
* **标识**：展开时 `SiLogo`（nav 尺寸，不显示副标）；收起时只显示图形标志。
* **新建会话**：普通按钮（非主色），点击同现在智能助手页的“新建会话”（创建后进入该会话）。
* **菜单顺序**：智能助手（`/assistant`）、智能审核（`/review/new`）、审核历史（`/history`）。选中项：浅主色底 + 主色文字 + 600 字重，圆角 `--el-border-radius-base`。选中判断沿用现有 `activeMenu` 的前缀规则（`/review/:id` 归“智能审核”）。
* **搜索**：一行菜单样式的按钮“搜索”，右侧小字 `Ctrl K`，点击打开全局搜索（第六节）。
* **近期会话**（只在展开时显示）：标题“近期会话”（`--si-font-meta`、次要色），列出本人最近 20 个会话（现有 `GET /assistant/sessions` 第 1 页，按最后活动排序），每行：生成中圆点 + 标题（单行省略，悬停 `title` 显示全名）；悬停出现“⋯”菜单：重命名、删除（沿用现有对话框文字与接口）。当前会话高亮。超过 20 个时末尾“查看更多”，追加下一页。会话列表放进新的 Pinia store `stores/sessions.ts`（`items`、`refresh()`、`loadMore()`），智能助手页在新建、改名、删除、发送后调用 `refresh()`。
* **设置**（`auth.isAdmin` 时显示）与**用户区**（首字圆形 + 显示名，点击下拉：修改密码、退出）放在侧栏底部；最下方一个收起 / 展开按钮（`PanelLeftClose` / `PanelLeftOpen` 图标，`aria-label` 写“收起侧栏”“展开侧栏”）。
* **收起为窄栏**：只显示图标（新建会话 `Plus`、智能助手 `MessageSquare`、智能审核 `FileSearch`、审核历史 `History`、搜索 `Search`、设置 `Settings`、用户首字），每个图标用 `el-tooltip`（右侧）显示文字，并写 `aria-label`。近期会话不显示。状态写 `si.sidebar.collapsed`。
* **管理员提醒条**移到主区域顶部，行为不变（SPEC 6.7）。

### 2.2 首页与高度

* `router/index.ts`：`HOME_PATH` 改为 `/assistant`；未知路径、登录后默认、`/` 都去首页。
* 主区域 `.main`：`height: 100vh; overflow-y: auto`，页面在其中滚动。需要占满高度的页面（审核结果、智能助手）根元素改为 `height: 100%`，删除 `calc(100vh - 64px)`（`views/ReviewResult.vue`、`views/Assistant.vue`）。对话框中的 `calc(100vh - …)` 是相对视口的，不动。
* **强制改密**：`auth.mustChangePassword` 时主区域不渲染 `router-view`，只显示一句“请先修改初始密码”；改密对话框照旧不可关闭。路由守卫中“只能停留在首页”的判断不变（首页改为 `/assistant`）。

**文件范围**：`frontend/src/layouts/MainLayout.vue`、`frontend/src/components/SplitHandle.vue`（新）、`frontend/src/components/SidebarSessions.vue`（新，近期会话列表）、`frontend/src/stores/sessions.ts`（新）、`frontend/src/router/index.ts`、`frontend/src/views/ReviewResult.vue`、`frontend/src/views/Assistant.vue`（只改根元素高度）、`frontend/src/components/brand/SiLogo.vue`（如需“只显示图形”的属性）、`frontend/src/styles/common.css`。

## 三、智能助手：两栏、对话与输入框（V3-A-03，GPT-6.1 Sol，high）

### 3.1 页面

* `views/Assistant.vue` 去掉左侧会话列表（已移到侧栏），改为两栏：对话区（自适应）＋ 文件面板（320px，现有 `FilePanel`）。对话区顶部一行：会话标题（点击改名）、右侧“删除会话”，与现在相同。
* 没有选中会话时：对话区显示空状态（第四节 `EmptyState`）：标题“开始一个新会话”，说明“可以直接提问，也可以先加入文件。发给模型的只有脱敏后的文字，回复在本机还原。”，操作“新建会话”。
* `components/assistant/SessionList.vue` 不再使用，删除；其中的会话搜索由全局搜索替代。

### 3.2 消息（`Conversation.vue`、`MessageItem.vue`）

* 对话内容最大宽度 760px、水平居中；正文 `--si-font-reading`（15px / 1.8）。
* 用户消息：靠右的气泡（浅主色底、`--si-radius-card` 圆角、最大宽度 80%）。助手回复：满宽、无边框、无底色。
* 回复的操作（复制、导出 Word、导出对照 Word、导出 Excel）放在回复下方一行，**鼠标悬停该条回复或键盘聚焦其中时显示**，其余时间隐藏（`opacity`，保持占位，不跳动）；最后一条回复始终显示。“停止”“重试”始终显示。
* **自动跟随**：生成中新增内容时滚动到底部；用户向上滚动（距底部 > 80px）即停止跟随，并在输入框上方居中显示“回到底部”小按钮（`ArrowDown` 图标），点击后回到底部并恢复跟随。切换会话时滚到底部。
* 生成中状态：回复末尾显示一行次要色小字“正在生成…”，不用转圈。

### 3.3 输入框（`Composer.vue`）

* 输入框 `type="textarea"`，`autosize: { minRows: 1, maxRows: 8 }`，`maxlength="4000"`，字数只在超过 3,500 字后显示。占位文字“输入问题或粘贴来函”。
* **Enter 打开“确认发送内容”**（即现在点“发送”的行为，不是直接发送），**Shift+Enter 换行**。输入法组字期间（`event.isComposing` 或 `keyCode === 229`）按 Enter 不处理。去掉原来的 Ctrl+Enter。输入框下方小字：“Enter 发送，Shift+Enter 换行”。
* 发送按钮在输入框右下角（`ArrowUp` 图标 + `aria-label="发送"`）；本会话有回复正在生成时原位变为“停止”（`Square` 图标 + `aria-label="停止生成"`），点击调用现有停止接口。
* 输入框下方一行（左侧）：模板选择、“附带公司知识库”“参考我的历史合同”两个勾选（现有控件移来，不改逻辑）。V3-B 在这一行加模型选择，本任务在行内留出位置即可。

**文件范围**：`frontend/src/views/Assistant.vue`、`frontend/src/components/assistant/Conversation.vue`、`MessageItem.vue`、`Composer.vue`、`TemplatePicker.vue`、`SessionList.vue`（删除）、`frontend/src/stores/sessions.ts`（只调用 `refresh()`）。

## 四、审核结果分栏、状态 / 空状态 / 提示、表格细节（V3-A-04，GPT-6.1 Sol，medium）

### 4.1 可拖动分栏（`views/ReviewResult.vue`）

* 左右两栏之间放 8px 宽的分隔条 `components/SplitHandle.vue`（V3-A-02 建，通用：属性为最小值、最大值、默认值与偏好键名）：悬停与拖动时显示 2px 主色竖线，`cursor: col-resize`；`role="separator"`、`aria-orientation="vertical"`、`aria-label`（此处“调整左右宽度”）、`tabindex="0"`，←/→ 每次 16px；双击恢复默认。
* 左栏默认仍为 `clamp(480px, 42%, 640px)`；拖动范围：左栏 ≥ 400px 且右栏 ≥ 420px。宽度写 `si.review.leftWidth`（像素数），读取后按当前容器宽度夹到范围内。窗口变窄时同样夹取。

### 4.2 状态标签与空状态

* `utils/labels.ts` 增加任务处理状态的标签（用在审核历史的状态列、智能助手文件面板的文件状态）：解析中 `info`、待确认 `warning`、已确认 `success`、失败 `danger`，均为浅底（默认 `effect`）；只读、已过期为 `info` + `effect="plain"`。frontend.md 第五节中已有的枚举（严重程度实心、引用状态描边等）不变。历史列表状态列由纯文字改为上述标签。
* 新组件 `components/EmptyState.vue`：Lucide 图标（48px、次要色）＋ 标题（15px / 600）＋ 一句原因（14px 次要色）＋ 可选操作插槽。替换全部页面级的 `el-empty`：审核历史无数据、智能助手无会话 / 未选会话、全局搜索无结果、审核结果问题清单为空、设置各列表无数据时的面板。表格内的 `empty-text` 也改为同样的“原因 + 可选操作”写法（用 `#empty` 插槽放 `EmptyState` 的紧凑尺寸）。

### 4.3 短提示

* `ElMessage` 统一显示在右下角：在 `main.ts` 或统一封装中设置 `placement: 'bottom-right'`（Element Plus 2.14.7 的 `ElMessage` 支持 `placement`；以其支持的全局配置方式为准，没有全局配置时在 `utils/` 中封装 `toast.success / toast.error` 并替换全部直接调用）。
* 提示文字、时长、用法（只用于用户刚点击的操作）不变。
* “撤销”类提示：本批不做（后端没有撤销接口；初稿写明“单独评估”）。

### 4.4 表格

适用：审核历史、外发记录、审计日志、知识库、法规库、公司范本、账号、已记住的名称、自定义敏感词、公司补充要求。

* **表头固定**：表格放在面板内、给 `el-table` 设 `max-height`（面板可用高度，至少 480px），超出时表体滚动、表头固定。
* **行尾操作**：常用的一两个操作直接显示为文字按钮，其余收进最后的“⋯”下拉（`MoreHorizontal` 图标，`aria-label="更多操作"`）；删除放在下拉最后、危险色，二次确认不变。每张表哪些算“常用”，按下表：

  | 表 | 直接显示 | 收进“⋯” |
  |-|-|-|
  | 审核历史 | 打开 | 删除 |
  | 账号 | 重置密码 | 修改角色、停用 / 启用 |
  | 知识库、公司范本 | 启用 / 停用 | 编辑（范本的条款立场）、删除 |
  | 法规库 | 查看 | 填写现行规定、其他已有操作 |
  | 已记住的名称、自定义敏感词、补充要求 | 编辑（如有） | 启用 / 停用、删除 |

  表中没有列出的已有操作，放进“⋯”。
* **长名称**：文件名、会话标题、法规名称、范本名称、知识库文件名列加 `show-overflow-tooltip`，单行省略，悬停显示全名。
* **首次加载骨架**：上述表格首次加载时显示同形状骨架（`el-skeleton`，行数 5、列宽与真实表格一致），之后的翻页、筛选用 `v-loading`。frontend.md 中“骨架屏只用于审核历史与审核结果”相应放宽。

**文件范围**：`frontend/src/main.ts`、`frontend/src/utils/toast.ts`（新，如需封装）与全部 `ElMessage` 调用处（只改调用方式）、`frontend/src/views/ReviewResult.vue`、`frontend/src/components/EmptyState.vue`（新）、`frontend/src/components/TableSkeleton.vue`（新）、`frontend/src/utils/labels.ts`、`frontend/src/views/History.vue`、`frontend/src/views/settings/` 下的列表页（Users、Data 的外发记录与审计日志、Laws、Templates、Knowledge、Redaction、Requirements 的列表部分）、`frontend/src/components/assistant/FilePanel.vue`（状态标签）、问题清单所在组件（空状态）。

## 五、设置分节与未保存提示（V3-A-05，GPT-6.1 Sol，medium）

* 新组件 `components/SettingSection.vue`：两列网格，左 240px（标题 15px / 600 + 一两行次要色说明），右侧为字段；节与节之间 1px `--el-border-color-lighter` 分隔线，上下各 24px；不再在面板里套卡片。1280 宽以下改为上下排列。
* `SettingSection` 另有 `stacked` 形式：标题与说明在上一行、内容占满宽度，用于放表格的节（表格放在两列的右侧太窄）。
* 新组件 `components/UnsavedBar.vue`：页面中有表单与服务端值不同时，在页面底部出现粘底提示栏：左侧“有未保存的修改”，右侧“放弃”“保存”。“保存”依次调用该页各个有改动的节原有的保存接口；某一节失败时停下、显示该接口的错误，已保存的节恢复为干净状态，未保存的保持；“放弃”把全部节恢复为服务端值。各节原有的“保存”按钮去掉，统一由提示栏保存。立即生效的控件（开关、下拉，frontend.md 第七节第 5 条）不进入未保存状态。
* 离开保护：有未保存修改时，`onBeforeRouteLeave` 弹出确认“有未保存的修改，离开后将丢失。”（按钮“离开”“留在此页”），设置左侧导航切换分类时同样提醒；同时设 `beforeunload` 提示。
* 八类设置逐页改写结构，字段、接口与校验不变：

  | 页 | 节（括号内为 `stacked`） |
  |-|-|
  | 账号 | （账号列表） |
  | 模型 | V3-B-03 整页重做时直接用本节组件，本任务不改 |
  | 审核要求 | 公司说明；（内置要求）；（公司补充要求） |
  | 脱敏 | 数量脱敏；（我方主体）；（自定义敏感词）；（已记住的名称） |
  | 法规库 | 审核时附带相关法条；导入目录与全文；（导入记录）；（法规列表） |
  | 公司范本 | （范本列表） |
  | 知识库 | 知识库开关；（资料列表） |
  | 数据 | 备份；数据保留；法规库自动更新；系统状态；（外发记录）；（审计日志） |

* 每节的标题与说明文字沿用页面现有的面板标题与说明；没有说明的节补一句用途说明（不超过 40 字）。

**文件范围**：`frontend/src/components/SettingSection.vue`（新）、`frontend/src/components/UnsavedBar.vue`（新）、`frontend/src/views/settings/` 下除 `Model.vue` 外的全部页面与 `SettingsLayout.vue`（切换分类时的提醒）。

## 六、全局搜索（V3-A-06，GPT-6.1 Sol，medium）

### 6.1 接口

`POST /api/search`，请求 `{ "q": "…" }`（去首尾空白后 1～50 字；不用查询参数，避免搜索词进入访问日志与地址）。

* 只查当前用户（`owner_id`）的任务：`tasks.title ILIKE '%q%'`（转义 `%`、`_`、`\`），`kind` 为 review 与 assist 各取最近活动的前 10 条。
* 返回 `{ "reviews": [{id, title, status, created_at}], "sessions": [{id, title, last_activity_at}] }`。管理员同样只看到本人的。
* 不写审计、不记日志中的 `q`。

### 6.2 界面（`components/GlobalSearch.vue`）

* 打开方式：侧栏“搜索”、`Ctrl+K`（Mac 为 `⌘K`；在 MainLayout 监听 `keydown`，`preventDefault`；强制改密期间不响应）。
* `el-dialog`（宽 640px、顶部 15vh、无标题栏）：顶部输入框（`Search` 图标、`aria-label="搜索"`），输入 300ms 后请求；下方分组：
  * **操作**：新建审核、新建会话（始终显示，按输入过滤）；
  * **页面**：智能助手、智能审核、审核历史，以及有权限的设置分页（按输入过滤，本地完成）；
  * **审核**、**会话**：接口结果，每行标题 + 次要色日期。
* ↑/↓ 移动、Enter 打开、Esc 关闭；鼠标悬停同步高亮。审核任务的跳转与审核历史点击行一致；会话跳转 `/assistant/:id`。
* 无结果时 `EmptyState`：“没有找到匹配的审核或会话”。

### 6.3 测试

`backend/tests/test_search.py` 两个用例：① 只返回本人的任务（另一用户同名任务不出现），review 与 assist 分组正确；② 搜索词含 `%`、`_` 时按字面匹配。

**文件范围**：`backend/app/tasks/search.py`（新，路由与查询）、`backend/app/main.py`（注册路由一行）、`backend/tests/test_search.py`（新）、`frontend/src/api/search.ts`（新）、`frontend/src/components/GlobalSearch.vue`（新）、`frontend/src/layouts/MainLayout.vue`（打开入口与快捷键）。

## 七、安装到桌面与工作文件夹（V3-A-07，GPT-6.1 Sol，high）

### 7.1 安装到桌面

* `frontend/public/manifest.webmanifest`：`name` “商衡SI”、`short_name` “商衡SI”、`start_url` “/assistant”、`scope` “/”、`display` “standalone”、`theme_color` / `background_color`（共同约定第 4 条）、`icons`：192、512 两个 PNG（`purpose: any`）和一个 512 的 `maskable`。`index.html` 加 `<link rel="manifest" href="/manifest.webmanifest">`。
* 图标 PNG 由现有品牌图形（`public/favicon.svg` 或 SiLogo 图形）在本机用 Edge 无头截图生成后提交，不新增构建依赖。
* 不加 Service Worker、不做离线、不缓存业务数据（Edge / Chrome 安装不再要求 Service Worker）。
* 确认 Caddy 返回清单时的 `Content-Type`；如不是 `application/manifest+json` 且 Edge 不识别，在 `deploy/Caddyfile` 的前端静态文件段为 `*.webmanifest` 加一条 header。
* `docs/ops/运维说明.md` 增加“安装到桌面”一节：前提是本机已按 `http://服务器IP/cert/` 导入根证书；在 Edge 中打开系统 →“…”→“应用”→“将此站点作为应用安装”；或建桌面快捷方式，目标 `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=https://服务器IP/`；需要开机即开时在 `edge://apps` 中对该应用勾选“登录时自动启动”。不写组策略。

### 7.2 工作文件夹（初稿第三节，D5）

用浏览器的文件夹授权接口（`showDirectoryPicker`，Edge / Chrome，需 HTTPS 或 localhost）。浏览器不支持时不显示任何工作文件夹入口，上传、拖入、下载照旧。

* **选择与记住**：`utils/workFolder.ts` 提供 `pick()`、`current()`、`clear()`、`listFiles()`、`saveInto(blob, filename)`。`pick()` 调用 `showDirectoryPicker({ id: 'si-work', mode: 'readwrite' })`；句柄存本浏览器 IndexedDB（库 `si`、表 `prefs`、键 `workFolder`），每个浏览器一个当前工作文件夹。每次使用前 `queryPermission`，未授权时在用户点击中 `requestPermission`；被拒绝时提示“没有该文件夹的访问权限，请重新选择”。
* **入口**：新建审核第一步上传区上方、智能助手文件面板“加入文件”旁，各一行：“工作文件夹：{文件夹名} · 更换 · 不使用”；未选时为“选择工作文件夹”文字按钮。文件夹名只在本页显示。
* **列出与点选**：点“从工作文件夹选择”打开对话框，列出该文件夹第一层中扩展名在支持格式内（SPEC 1.4）的文件：文件名、大小、修改时间，按修改时间倒序；勾选后点“加入”，读取为 `File` 后走与拖入完全相同的上传代码（数量、大小检查不变）。**选定或打开文件夹不触发任何上传**，只有勾选并点“加入”的文件才上传。
* **保存导出结果**：所有导出（审核报告、助手的 Word / 对照 Word / Excel、V3-C 的批注版）统一经 `utils/save.ts` 的 `saveBlob(blob, filename)`：有工作文件夹且已授权时写入该文件夹，同名时依次改为“名称 (2).docx”“名称 (3).docx”，**绝不覆盖**，成功提示“已保存到工作文件夹：{新文件名}”；没有工作文件夹、未授权或写入失败时按普通下载处理（失败时另提示原因）。
* **隐私**：文件夹路径、文件夹名、列表中的文件名不发送到服务器、不写日志、不进 URL；上传的文件本身的文件名照现行规则处理。任务数据仍在服务器，文件夹中的文件移动或删除不影响任务。

**文件范围**：`frontend/public/manifest.webmanifest`（新）、`frontend/public/icons/`（新，3 个 PNG）、`frontend/index.html`、`deploy/Caddyfile`（仅在需要时）、`docs/ops/运维说明.md`（新增一节，含工作文件夹的说明）、`frontend/src/utils/workFolder.ts`（新）、`frontend/src/utils/save.ts`（新）、`frontend/src/components/WorkFolderBar.vue`（新，入口一行）、`frontend/src/components/WorkFolderPicker.vue`（新，列出与点选对话框）、`frontend/src/views/ReviewNew.vue`、`frontend/src/components/assistant/FilePanel.vue`、`frontend/src/api/report.ts`、`frontend/src/components/assistant/MessageItem.vue`。

## 八、集中测试（V3-A-08，GPT-6.1 Sol，medium）

1. `npm --prefix frontend run build`；整仓全量测试一次（含 `test_frontend_style.py`）。
2. 用 `.claude/launch.json` 的 frontend-dev 在浏览器面板中按下面清单检查，每项截图一张存 `docs/v3/reports/V3-A/`（只用虚构数据）：
   * 1280×800、1440×900、1920×1080：侧栏展开 / 收起，智能助手、新建审核、审核历史、审核结果、设置（数据页）各一张；收起状态刷新后保持。
   * 窄栏图标的悬停提示；近期会话的生成中圆点、改名、删除、查看更多。
   * 智能助手：Enter 打开确认、Shift+Enter 换行、中文输入法组字时按 Enter 不发送；生成中“停止”；上翻后出现“回到底部”。
   * 侧栏拖宽与审核结果拖动分栏：拖动、键盘调整、双击恢复，刷新后保持。
   * 表格：表头固定、行尾“⋯”、长名称悬停、首次加载骨架；短提示在右下角。
   * 设置八类：分节结构；改动后出现底部提示栏，“保存”“放弃”，切换分类与离开时提醒。
   * 全局搜索：Ctrl+K、键盘选择、本人范围（换一个用户登录确认看不到）。
   * 工作文件夹：选择、只列出支持的格式、选定后无上传请求（看网络请求）、勾选加入后正常上传；导出写入文件夹、同名加序号不覆盖；撤销授权后回退为下载。
   * 强制改密账号登录：主区域不显示业务页面。
3. 安装到桌面：在测试虚拟机或本机 compose（HTTPS，已导入根证书）中用 Edge 安装一次，截图应用窗口；不能做时在报告中写明原因，留到 V3-Z 演练。
4. 写 `docs/v3/reports/V3-A-test.md`（写短），完成后停下交 Opus。

## 九、审核与合并（V3-A-09，Opus，high）

审阅 V3-A-01～08；按第十节把规格变更合并进 docs/SPEC.md、docs/design/frontend.md；按 PLAN 第六节改 CLAUDE.md、AGENTS.md 的菜单表述。版本号不变。

## 十、规格变更（V3-A-09 合并）

**docs/SPEC.md**

* **1.6 菜单结构**：改为“左侧侧栏：新建会话；三个菜单 **智能助手**、**智能审核**、**审核历史**（13A）；搜索；近期会话；底部为系统管理员、业务管理员可见的**设置**入口与用户菜单（修改密码、退出）。侧栏可收起为图标窄栏。默认首页为智能助手。不得增加其他菜单。”
* **3 技术栈**：前端依赖增加 `@lucide/vue`（精确版本以 package.json 为准）。
* **6.8 界面风格**之后新增 **6.9 主布局与全局搜索**：侧栏结构（第二节）、强制改密时主区域不显示业务页面、全局搜索的入口与范围（只搜本人的审核与会话标题，第六节）、工作文件夹（7.2：选定不上传、只上传勾选的文件、导出写入文件夹且不覆盖、路径与文件名不发送到服务器）。
* **13A 第 2 条**：增加“输入框 Enter 打开发送确认、Shift+Enter 换行；生成中发送按钮变为停止”。
* **19 接口**：19.3 增加 `POST /search`（本人范围）。
* **20.4 HTTPS** 之后增加一句：导入根证书后可在 Edge 中安装为应用，步骤见运维说明。
* **23.2**：增加 T-61 全局搜索只返回本人任务。

**docs/design/frontend.md**

* 2.1 主布局：按第二节重写（侧栏默认 232px、可拖宽 200～320px、收起 64px、菜单顺序、近期会话、设置与用户区在底部、提醒条在主区域顶部）；删除“顶栏高度一律按 64px 计”，改为“需要占满高度的页面用 `height: 100%`，由主区域提供高度”。
* 2.2：`.page--split` 增加可拖动分栏（4.1）；智能助手页为对话区 + 文件面板两栏，对话最大宽度 760px。
* 2.3：“顶栏选中项已说明位置”改为“侧栏选中项”；设置八类页面用 `SettingSection` 分节（含 `stacked`）与底部 `UnsavedBar`（第五节）。
* 第四节组件表：增加 `SplitHandle`、`EmptyState`、`TableSkeleton`、`SettingSection`、`UnsavedBar`、`GlobalSearch`、`WorkFolderBar`、`WorkFolderPicker`；“没有内容”一行改为 `EmptyState`；数据列表一行增加表头固定、行尾“⋯”菜单（4.4 表）、长名称省略、首次加载骨架；“操作完成的短反馈”一行写明显示在右下角；“不要使用”中骨架屏的范围按 4.4 放宽。
* 图标规则改为：“只用 `@lucide/vue`（本地打包），默认 16px、线宽 1.75、继承文字色；图标旁必须有文字，窄栏与纯图标按钮例外，须有悬停提示与 `aria-label`”。
* 第五节：增加任务处理状态的标签（4.2）。
* 新增一条偏好存储约定（共同约定第 2 条）。

## 十一、任务拆分

| 编号 | 任务 | 模型 | 强度 | 依赖 |
|-|-|-|-|-|
| V3-A-01 | Lucide 图标（第一节） | GPT-6.1 Sol | medium | V3-00 用户确认 |
| V3-A-02 | 侧栏主布局与首页（第二节） | GPT-6.1 Sol | high | V3-A-01 |
| V3-A-03 | 智能助手两栏、对话与输入框（第三节） | GPT-6.1 Sol | high | V3-A-02 |
| V3-A-04 | 审核结果分栏、状态 / 空状态 / 提示、表格细节（第四节） | GPT-6.1 Sol | medium | V3-A-03 |
| V3-A-05 | 设置分节与未保存提示（第五节） | GPT-6.1 Sol | medium | V3-A-04 |
| V3-A-06 | 全局搜索（第六节） | GPT-6.1 Sol | medium | V3-A-05 |
| V3-A-07 | 安装到桌面与工作文件夹（第七节） | GPT-6.1 Sol | high | V3-A-06 |
| V3-A-08 | 集中测试（第八节） | GPT-6.1 Sol | medium | V3-A-07 |
| V3-A-09 | 审核与合并规格（第九、十节） | Opus | high | V3-A-08 |
