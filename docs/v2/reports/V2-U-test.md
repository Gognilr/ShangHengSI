# V2-U 集中测试报告

2026-10-27 · 实现与首轮检查：GPT-6.1 Terra（用户同意）；收尾：GPT-6.1 Sol。

## 当前结论

U-01～U-07 已分别提交。整仓测试 3084 passed、退出码 0，前端构建及最终样式检查通过；主要页面四宽度截图与关键交互已核对。用户于2026-10-27确认V2Q-114：接受1280px窗口约35字/行的例外，保持现有布局。**U-08 已完成，等待 V2-U-09（Opus，high）整批审核**。此结论是开发验证完成，不是Opus审核通过或正式部署验收；未进入V2-D。

## 环境与命令

前端通过本机 Vite 访问独立 `aireview-e08-ui-app`（127.0.0.1:8197）；数据为既有虚构 UI 样本和本次新增的虚构状态，不连接正式服务器，不调用真实模型。处理状态直接建立测试记录，未创建后台模型作业。法规导入和外发记录详情的个别显示分支使用浏览器响应模拟，见下表；不将这些检查视为真实导入或真实模型验证。

```powershell
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-e04-test-db:5432/postgres -v "${PWD}:/srv" -w /srv/backend aireview-backend-test:e04 pytest -q
npm --prefix frontend run build
docker run --rm -v "${PWD}:/srv" -w /srv/backend aireview-backend-test:e04 pytest -q tests/test_frontend_style.py tests/test_forbidden_words.py
```

- 整仓：**3084 passed、1 warning，1498.60 秒，退出码 0**。日志 `.codex-tmp/v2u/full-test-pgvector.log`，退出码 `.codex-tmp/v2u/full-test-pgvector.exit`。警告为既有 Starlette/httpx 弃用提示。
- `aireview-test-db` 实际仍为普通 postgres:16.15，保留未覆盖；本次改用已存在的 `aireview-e04-test-db`（aireview-db:dev）。最初对旧库发起的进程已停止，不计为通过。默认旧测试镜像缺 pgvector，采用已有 e04 测试镜像，未下载或重建镜像。
- 整仓运行期间修正了前端角色列、Teleport 样式命中与卡片间距。整仓结果覆盖后端和全部收集用例；这些最后的纯前端修正后又分别通过前端 build、27 项规范/禁用措辞检查。不能将此描述成在最终代码快照上重新启动的第二次整仓测试。
- 前端 build 含 vue-tsc，通过；Vite 有大于 500 kB 构建块的提示。U-01 测试先行记录为修改前 5 failed、20 passed，之后补充扫描器自检，最终两个检查文件 27 passed。

## 浏览器检查

窗口：1280×800、1366×768、1440×900、1920×1080。主要页面均有四宽度截图；以下记录区分实际后端虚构样本和响应模拟。

| 项目 | 结果与证据 |
|-|-|
| 结果页首屏 | 1366×768 可见结论、第一张问题标题和原文引用。主内容区 clientHeight=scrollHeight；长正文样本左右栏均可独立滚动，滚动条为 thin。见 `result-final2-*`、`result-metrics.json`、`long-doc-check.js` 的执行回执 |
| 工具条与等级 | 追问、重新审核可打开；虚构合同导出出现“报告已下载”；删除只验证确认并取消。等级筛选与再点取消正常。未完成轮次导出禁用，第二行可见原因 |
| 编号与轮次 | 筛选后原“02”保持不变；点击问题后原文显示“正在定位：问题 01”。虚构两轮切换可显示对应问题与状态。备注添加、回车保存、修改后自动聚焦正常 |
| 结果状态 | 虚构失败、部分完成、仅本地校验、原件四天后到期状态均已打开核对；到期文案为“原件将于 … 删除，之后不能重新审核和追问” |
| 原文阅读 | 15px / 27px，pre-wrap 保留连续空格和段内换行；长正文定位上下留白约 225px，超过 48px。XLSX 单元格、PDF canvas、PNG 原图均显示。行长精确值见 V2Q-114，1280px约35.2字按用户确认的窄窗口例外处理 |
| 智能审核 | 第一页无重复标题；四宽度页面无横向溢出，主滚动区 right=窗口宽度，步骤条距顶栏下方 24px。确认页主滚动区管理滚动，操作栏贴底。解析/审核/排队状态有计时、说明和返回入口；解析显示文件清单，不显示百分比和转圈；审核显示模拟第2/5段与既有摘要计数 |
| 减少动态效果 | 解析进度条 animation=none，宽 280px / 总宽560px，静止半段；恢复模拟前的媒体偏好 |
| 历史与加载 | 筛选和表格在同一面板；空查询显示“没有审核记录”。受控网络延迟下历史与结果首次加载骨架可见，完成后消失。不存在任务显示预期404提示 |
| 设置页 | 七页×四宽度共28组滚动测量：document.scrollWidth=窗口宽度，main右边缘贴窗口；带两条管理员提醒时底部内容可见，设置导航保持sticky |
| 标题、焦点 | 已打开新增账号、重置密码、公司补充要求、公司范本上传、修改密码、重新审核、审核要求、手动选择、疑似遗漏分类、追问、追问发送确认与法规差异等对话框/抽屉；抽查标题18px/600/26.1px。顶栏菜单、设置、用户入口和上传入口Tab外环为深主色2px |
| 法规导入（响应模拟） | 原隔离库有pending记录，导入按钮按业务禁用。仅在浏览器将GET导入记录列表模拟为空，检查目录/全文导入弹窗四宽度后取消；未上传文件，未改原pending记录，已撤销拦截 |
| 外发详情（响应模拟） | 浏览器模拟一条虚构日志及其详情，四宽度确认抽屉宽560px、贴窗口右侧、标题令牌生效；已撤销拦截。初次模拟详情路由未命中产生预期404，修正测试匹配后通过 |
| 控制台与请求 | 正常页面检查的pageerror记录为空。人工构造404、登录前401、账号切换时logout请求中断、测试截图超时及测试选择器失败均保留，不将它们混作产品正常路径已通过的证据 |

## 集中测试发现并修复

1. 角色列截断“系统与业务管理员”：列宽140px，四宽度复查完整。提交 `02d81eb`。
2. Teleport后的局部选择器未命中弹窗和抽屉：改用组件类名限定选择器，复测18px/600/26.1px。提交 `e265a83`。
3. 典型卡片高度超目标：收紧间距后，含引用、问题、影响、建议的虚构单行样本在1366px下236.6px；额外法条/范本信息或长文本会更高。提交 `ac877e1`。

修复均在各原任务文件范围内完成；未改接口、后端业务、设计文档或既有回归样本。

## 截图与来源

截图不入仓库，保存在 `.codex-tmp/v2u/before/` 和 `after/`。下列每组均有1280、1366、1440、1920四档。

| 页面 | 改前组 | 改后组 |
|-|-|-|
| 登录 | `before/login-*` | `after/login-after-*` |
| 上传与设置 | `before/new2-*` | `after/new-after-*` |
| 确认脱敏 | `before/confirm-*` | `after/confirm-after-*` |
| 解析中 | `before/retro-parsing-*` | `after/parsing-after-*` |
| 审核中 | `before/retro-running-*` | `after/running-after-*` |
| 历史 | `before/history-*` | `after/history-after-*` |
| 结果 | `before/result-*` | `after/result-final2-*` |
| 账号 | `before/settings-users-*` | `after/users-final-*` |
| 其余设置 | `before/settings-{model,requirements,laws,templates,redaction,data}-*` | `after/after-settings-{model,requirements,laws,templates,redaction,data}-*` |

主要页面的1366、1920图均按上述同组文件配对。结果页示例：

![改前结果页1366](../../../.codex-tmp/v2u/before/result-1366.png)
![改后结果页1366](../../../.codex-tmp/v2u/after/result-final2-1366.png)
![改后结果页1920](../../../.codex-tmp/v2u/after/result-final2-1920.png)

实际动手前采集的基线为登录、新建审核、历史、结果和七个设置页。确认页复用E-08保存截图，并用 `git diff 79c7122 1c30d24 -- frontend` 验证前端源码一致。解析中/审核中基线为改动后从提交 `1c30d24` 归档重建的旧前端补拍，使用同一虚构状态记录，明确不算动手前原始截图。`before/new-1280.png` 来自失联旧页面，不能使用，以`new2-*`为准。旧版补拍服务已停止，资料保留。

## 主要页面截图配对

<details><summary>登录</summary>

![登录 改前 1366](../../../.codex-tmp/v2u/before/login-1366.png)

![登录 改后 1366](../../../.codex-tmp/v2u/after/login-after-1366.png)

![登录 改前 1920](../../../.codex-tmp/v2u/before/login-1920.png)

![登录 改后 1920](../../../.codex-tmp/v2u/after/login-after-1920.png)

</details>

<details><summary>上传与设置</summary>

![上传与设置 改前 1366](../../../.codex-tmp/v2u/before/new2-1366.png)

![上传与设置 改后 1366](../../../.codex-tmp/v2u/after/new-after-1366.png)

![上传与设置 改前 1920](../../../.codex-tmp/v2u/before/new2-1920.png)

![上传与设置 改后 1920](../../../.codex-tmp/v2u/after/new-after-1920.png)

</details>

<details><summary>确认脱敏</summary>

![确认脱敏 改前 1366](../../../.codex-tmp/v2u/before/confirm-1366.png)

![确认脱敏 改后 1366](../../../.codex-tmp/v2u/after/confirm-after-1366.png)

![确认脱敏 改前 1920](../../../.codex-tmp/v2u/before/confirm-1920.png)

![确认脱敏 改后 1920](../../../.codex-tmp/v2u/after/confirm-after-1920.png)

</details>

<details><summary>解析中（旧版基线补拍）</summary>

![解析中（旧版基线补拍） 改前 1366](../../../.codex-tmp/v2u/before/retro-parsing-1366.png)

![解析中（旧版基线补拍） 改后 1366](../../../.codex-tmp/v2u/after/parsing-after-1366.png)

![解析中（旧版基线补拍） 改前 1920](../../../.codex-tmp/v2u/before/retro-parsing-1920.png)

![解析中（旧版基线补拍） 改后 1920](../../../.codex-tmp/v2u/after/parsing-after-1920.png)

</details>

<details><summary>审核中（旧版基线补拍）</summary>

![审核中（旧版基线补拍） 改前 1366](../../../.codex-tmp/v2u/before/retro-running-1366.png)

![审核中（旧版基线补拍） 改后 1366](../../../.codex-tmp/v2u/after/running-after-1366.png)

![审核中（旧版基线补拍） 改前 1920](../../../.codex-tmp/v2u/before/retro-running-1920.png)

![审核中（旧版基线补拍） 改后 1920](../../../.codex-tmp/v2u/after/running-after-1920.png)

</details>

<details><summary>审核历史</summary>

![审核历史 改前 1366](../../../.codex-tmp/v2u/before/history-1366.png)

![审核历史 改后 1366](../../../.codex-tmp/v2u/after/history-after-1366.png)

![审核历史 改前 1920](../../../.codex-tmp/v2u/before/history-1920.png)

![审核历史 改后 1920](../../../.codex-tmp/v2u/after/history-after-1920.png)

</details>

<details><summary>审核结果</summary>

![审核结果 改前 1366](../../../.codex-tmp/v2u/before/result-1366.png)

![审核结果 改后 1366](../../../.codex-tmp/v2u/after/result-final2-1366.png)

![审核结果 改前 1920](../../../.codex-tmp/v2u/before/result-1920.png)

![审核结果 改后 1920](../../../.codex-tmp/v2u/after/result-final2-1920.png)

</details>

<details><summary>账号</summary>

![账号 改前 1366](../../../.codex-tmp/v2u/before/settings-users-1366.png)

![账号 改后 1366](../../../.codex-tmp/v2u/after/users-final-1366.png)

![账号 改前 1920](../../../.codex-tmp/v2u/before/settings-users-1920.png)

![账号 改后 1920](../../../.codex-tmp/v2u/after/users-final-1920.png)

</details>

<details><summary>模型</summary>

![模型 改前 1366](../../../.codex-tmp/v2u/before/settings-model-1366.png)

![模型 改后 1366](../../../.codex-tmp/v2u/after/after-settings-model-1366.png)

![模型 改前 1920](../../../.codex-tmp/v2u/before/settings-model-1920.png)

![模型 改后 1920](../../../.codex-tmp/v2u/after/after-settings-model-1920.png)

</details>

<details><summary>审核要求</summary>

![审核要求 改前 1366](../../../.codex-tmp/v2u/before/settings-requirements-1366.png)

![审核要求 改后 1366](../../../.codex-tmp/v2u/after/after-settings-requirements-1366.png)

![审核要求 改前 1920](../../../.codex-tmp/v2u/before/settings-requirements-1920.png)

![审核要求 改后 1920](../../../.codex-tmp/v2u/after/after-settings-requirements-1920.png)

</details>

<details><summary>法规库</summary>

![法规库 改前 1366](../../../.codex-tmp/v2u/before/settings-laws-1366.png)

![法规库 改后 1366](../../../.codex-tmp/v2u/after/after-settings-laws-1366.png)

![法规库 改前 1920](../../../.codex-tmp/v2u/before/settings-laws-1920.png)

![法规库 改后 1920](../../../.codex-tmp/v2u/after/after-settings-laws-1920.png)

</details>

<details><summary>公司范本</summary>

![公司范本 改前 1366](../../../.codex-tmp/v2u/before/settings-templates-1366.png)

![公司范本 改后 1366](../../../.codex-tmp/v2u/after/after-settings-templates-1366.png)

![公司范本 改前 1920](../../../.codex-tmp/v2u/before/settings-templates-1920.png)

![公司范本 改后 1920](../../../.codex-tmp/v2u/after/after-settings-templates-1920.png)

</details>

<details><summary>脱敏</summary>

![脱敏 改前 1366](../../../.codex-tmp/v2u/before/settings-redaction-1366.png)

![脱敏 改后 1366](../../../.codex-tmp/v2u/after/after-settings-redaction-1366.png)

![脱敏 改前 1920](../../../.codex-tmp/v2u/before/settings-redaction-1920.png)

![脱敏 改后 1920](../../../.codex-tmp/v2u/after/after-settings-redaction-1920.png)

</details>

<details><summary>数据</summary>

![数据 改前 1366](../../../.codex-tmp/v2u/before/settings-data-1366.png)

![数据 改后 1366](../../../.codex-tmp/v2u/after/after-settings-data-1366.png)

![数据 改前 1920](../../../.codex-tmp/v2u/before/settings-data-1920.png)

![数据 改后 1920](../../../.codex-tmp/v2u/after/after-settings-data-1920.png)

</details>

## 已确认的例外与审核交接

V2Q-114：1280px实际约35.2字/行；1366px约38.5字，1440/1920px约39.3字。用户已确认接受1280px约35字/行的例外，保持现有布局。未再修改源代码，沿用本报告记录的测试结果，无需为该文档确认重复运行测试。

下一步V2-U-09，由Opus（high）核对文件范围、测试未被删改、截图来源与用户确认的例外，并复核关键界面。当前会话停止，不执行Opus审核，也不进入V2-D。

## V2-U-09 批次审核（Opus 5.5，2026-10-27）

范围：1c30d24..4eef0c5，U-01～U-08 共 11 个提交（前端 30 个文件，+新测试 1 个）。

**测试与文件范围**：后端只新增 `tests/test_frontend_style.py`，未改任何已有测试、样本与后端代码；扫描器有自检用例，`font:` 简写中也未发现绕过白名单的写法。各提交只改所在任务卡的文件；集中测试中的三处修正都回到原任务文件范围内。

**逐项审阅结论**：

| 项 | 结论 |
|-|-|
| 令牌与全局样式 | 十个字体令牌与七个颜色 / 圆角令牌只在 theme.css；App.vue 的第二份字体已删；焦点外环改为深主色 |
| 一层滚动 | `.main` 负责滚动，ReviewNew 的 `.page` 高度与 overflow 已删；结果页工作台用 `max-height: 100%` 兜住，带两条管理员提醒时（本次浏览器实测）主区 clientHeight=scrollHeight=623，文档不滚动，只有左右两栏滚动 |
| 结果页工作台 | 工具条含返回、文件、轮次、追问、导出、重新审核（显示条件不变）、删除；元信息一行含范本名称、到期提示与导出原因。结论块是唯一的等级筛选入口，制裁固定声明保留。前端去掉的等级排序由后端 `run_detail` 保证（按等级、seq），编号与筛选无关 |
| 问题卡片 | 阅读顺序、编号、实心等级标签、`::before` 色条、依据与法条原文、“范本对照”“引用未经法规库核实”均在。处理区加 `@click.stop`，点下拉或备注不再触发原文定位，属于合理的交互修正 |
| 原文阅读区 | 纸张、15px / 1.8、40em、段距 12px、`pre-wrap` 保留；“正在定位：问题 0n”只在确有高亮时显示 |
| 解析中 / 审核中 | 不显示百分比与剩余时间；计时器离开页面清除；替换数取已有摘要接口，失败不显示；减少动态效果时静止。本次虚构合同实测排队页显示“排队中，前面还有 0 个任务”、已用时间、替换 22 处、返回按钮 |

**修复**：U-07 为让弹窗、抽屉标题在 Teleport 后命中，在 11 个组件中各写了一份 `:global(.u7-xxx …)` 规则并给每个弹窗加 `u7-` 类名。这种写法每新增一个弹窗都要复制一次，且已漏掉 ReviewNew 的“重复文件”弹窗。改为 theme.css 中一条全局规则（`.el-dialog__title, .el-drawer__title` 用 `--si-font-h2`，正文用 `--si-font-body`），删除全部 `u7-` 类名与重复规则。build 通过，规范检查与禁用措辞 27 passed；浏览器复核重新审核弹窗、追问抽屉、新增账号弹窗标题均为 18px / 600 / 26.1px。只改样式，未重跑整仓全量。

**不修、只记录**：① 只有文件摘要、没有限制事项时，折叠标题显示“文件摘要与限制事项（0 条）”，数字只数限制事项，略有歧义，留 V2-D 顺手处理；② 卡片高度 236.6px 是单行样本，位置较长或有法条时会超过 240px，属预期；③ 本机 `aireview-test-db` 仍是旧 postgres 镜像，CLAUDE.md 已写明重建方法，下次全量测试前应按其重建。

**结论**：V2-U 批次可以通过；V2Q-114（1280px 约 35 字 / 行）用户已确认。用户确认后进入 V2-D-00（Opus，xhigh）。

**用户确认**（2026-10-27）：V2-U 整批通过，进入 V2-D-00。
