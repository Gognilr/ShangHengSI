# 商衡SI（ShangHeng SI）

[English](README.md) | **简体中文** | [日本語](README.ja.md)

企业商务文件 AI 智能审查助手：在公司局域网内使用的商务文件初审工具，适用于合同、报价单、订单、询价函和各类函件。

**当前版本：V3.3.0**（2026-11-15 发布）。

敏感信息不出内网：文件在本地解析、校验并**自动脱敏**，发给云端大模型的只有脱敏后的文本；审核结果在本地还原成真实名称后再展示。

> ⚠️ 本工具只做**初审**，结果不能代替法务、合规和业务人员的审核。

## 工作流程

```text
上传文件 → 本地解析与校验 → 自动脱敏 → 用户确认 → 云端大模型初审
        → 本地还原并显示问题清单 → 追问与按问题讨论
        → 导出 Word 报告 / 批注版 / 修订版
```

1. **上传**：可上传多个文件，选择立场（销售 / 采购 / 其他）、来源（我方发出 / 对方发来）和外发策略。
2. **本地处理**：解析为统一的文档模型；本地校验金额、合计、日期、币种等；把企业名称、人名、金额、账号、电话等替换为“客户\_A”“\[金额\_1]”之类的代号。
3. **确认**：用户确认哪一方是“我方”，查看脱敏摘要，并预览实际将发送的文本。
4. **AI 审核**：模型对每一条审核要求给出结论，列出问题，并附位置编号和逐字引用的原文；引用是否准确在本地校验。
5. **查看结果**：问题清单显示还原后的真实名称，审核进行中流式呈现；可以继续追问（追问内容同样先脱敏再发送），按单个问题讨论并采纳讨论稿，导出 Word 报告、批注版或修订版。模型回复带引用锚点，点击后以浮层显示引用来源（知识库、历史记录或法规）。

外发策略为“不外发”（local\_only）的任务绝不调用模型，只做本地校验。

## 功能模块与新版亮点

侧栏共五项：

| 模块 | 说明 |
|-|-|
| 工作台 | 统一入口（V3.2 起由“智能助手”改称）：在同一会话里发起审核、就文件提问、按问题讨论 |
| 智能审核 | 上述文件审核流程 |
| 会议纪要 | 会议录音在本地转写并生成纪要 |
| 翻译 | 文件与粘贴文本翻译，本地运行 |
| 历史记录 | 全部历史审核任务与会话（V3.2 起由“审核历史”改称） |

V3.3.0 亮点：

- **统一的“页面不存在”页**：输错地址不再悄悄跳回首页，而是在主框架内打开“页面不存在”页，可直接回到工作台。
- **等待状态三案**：区块加载改为品牌三色审阅光带；文件解析显示逐行扫描、逐行脱敏的小纸面；对话等待改为墨点聚散指示。三类等待统一在 300ms 后才显现，快速返回不闪。
- **首页时段问候**：首页按北京时间与用户显示名给出时段问候语。
- **缺项提示与定位**：必选项未选时提交按钮保持可点，未选控件标出并加主色虚线框，点击后滚动定位到第一个缺项。
- **采纳 / 忽略后自动展开下一条**：处理完一条问题后自动收起本条并展开下一条待处理，与快捷键 A / I 一致。
- **引用锚点更稳**：知识库与历史记录的引用编号在会话内保持稳定，常见变体写法也能识别定位；已用真实模型验证达标（19 次调用）。V3.1.0 的会议纪要旧数据也已验证可直接升级使用，无代码改动。
- **技术词与型号识别**：工业控制类技术词不再被误报，产品型号保持整体识别、不被拆分，技术词处理推广到各行业。
- **外发附加泄漏提示**：外发附加内容中检测到的泄漏按节列出，全部处理完之前确认保持锁定。
- **增量升级包**：构建脚本自动查找可用的 Python 3.11+；升级包可只带改动过的镜像（V3.3.0 增量包约 1.8 GB，完整包约 9.4 GB），全新安装会拒绝增量包。

## 安全与隐私设计

- **唯一出网点**：只有 `backend/app/llm/gateway.py` 可以访问外网。网关依次执行：每月调用上限检查 → 出口复检 → 发送 → 记录 `llm_calls`。
- **出口复检**：发送前再扫描一遍，检查对照表中的全部真实值，以及未替换的电话、邮箱、账号、证件号、金额；发现即拦截，不发送。
- **只发文本**：不向模型发送图片和原文件。
- **加密存储**：原件、对照表、敏感字段用主密钥做 AES-256-GCM 加密。
- **严格的归属校验**：所有任务接口都校验所有者，非本人（包括管理员）一律返回 404。
- **日志干净**：日志中不出现原文、真实名称、金额和 API 密钥。

## 适用文件

| 类型 | 例子 |
|-|-|
| 合同 | 销售合同、采购合同、框架协议 |
| 报价单 | 报价单、形式发票 |
| 订单/供货单 | 采购订单、供货单 |
| 询价函 | 询价函、RFQ |
| 函件 | 供货函、承诺函、通知函、索赔函等 |

支持格式为 DOCX、PDF（文字版和扫描件）、XLSX、JPG、PNG；旧版 Office 格式由离线的 converter 服务在本地转换（见系统架构）。

审核维度：表述、逻辑、法律、合规与制裁、商务。

## 技术栈

| 层 | 选型 |
|-|-|
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router |
| 后端 | Python 3.12、FastAPI、Uvicorn、Pydantic v2 |
| 数据库与任务队列 | PostgreSQL 16（同时用 `SELECT … FOR UPDATE SKIP LOCKED` 承担任务队列） |
| ORM 与迁移 | SQLAlchemy 2、Alembic、psycopg 3 |
| 解析 | python-docx + lxml、openpyxl、pdfplumber、RapidOCR（onnxruntime CPU 推理） |
| 加密 | cryptography（AES-256-GCM）、argon2-cffi（argon2id） |
| 大模型 | 任意 OpenAI 兼容的 Chat Completions 接口（如 DeepSeek），只在网关中通过 `httpx` 调用 |
| 部署 | Docker Compose v2、Caddy 2（HTTPS 与静态文件） |

## 系统架构

7 个服务，全部运行在一台 Ubuntu Server 24.04 虚拟机中，定义见 `deploy/docker-compose.yml`：

```text
员工浏览器（局域网，HTTPS）
  └─> web     Caddy：前端静态文件 + 转发 /api
        └─> app     FastAPI：全部接口、追问对话
              ├─> db      PostgreSQL：数据 + 任务队列
              └─> /data/files（加密原件，共享卷）
worker    解析、OCR、本地校验、脱敏、AI 审核、备份、定时清理
          └─> 云端大模型接口（唯一允许的出站地址）
converter / transcriber / translator
          离线辅助服务，完全不接网络（network_mode: none），
          只通过共享的 /spool 卷与 worker 交换文件
          （旧格式转换、录音转写、本地翻译）
```

## 目录结构

```text
backend/    FastAPI 应用、worker、Alembic 迁移、资源文件、测试
frontend/   Vue 3 单页应用
converter/  离线旧格式转换服务（不接网络）
transcriber/ 离线录音转写服务（不接网络）
translator/ 离线本地翻译服务（不接网络）
deploy/     docker-compose.yml、Caddyfile、.env.example
docs/       SPEC.md（完整规格）、TASKS.md（任务清单）、open_questions.md、design/、
            各版本目录（v2/ … v33/）、acceptance/（安装与升级记录）、ops/（运维说明）
```

## 快速开始（开发环境）

需要 Docker（含 Compose v2）；开发前端还需要 Node.js。后端的运行、测试和迁移一律在 Docker 中进行。以下为 PowerShell 写法，在 bash 中把 `${PWD}` 换成 `$(pwd)`。

```powershell
# 构建运行镜像，生成开发用主密钥（目标文件已存在时会拒绝覆盖）
docker build -t aireview-backend:dev --target runtime backend
docker run --rm -v "${PWD}/deploy/secrets:/out" aireview-backend:dev python -m app.cli gen-key --out /out/master_key

# 准备环境变量文件，然后填写 POSTGRES_PASSWORD
# （本机 80/443 被占用时，把 HTTP_PORT/HTTPS_PORT 改为 8080/8443）
copy deploy\.env.example deploy\.env

# 启动整套系统（app 启动时自动执行 alembic upgrade head）
docker compose -f deploy/docker-compose.yml up -d --build
```

前端开发服务器（`/api` 代理到已启动的系统）：

```powershell
npm --prefix frontend ci --include=dev
npm --prefix frontend run dev        # http://localhost:5173
```

运行后端测试：

```powershell
docker build -t aireview-backend-test --target test backend
docker network create aireview-test
docker run -d --name aireview-test-db --network aireview-test -e POSTGRES_PASSWORD=test postgres:16.15
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```

`deploy/.env`、`deploy/secrets/`、`deploy/backup/` 都不进仓库。

## 项目状态

当前版本为 V3.3.0（2026-11-15 发布），已在生产服务器上运行。发布报告见 [docs/v33/reports/V33-Z.md](docs/v33/reports/V33-Z.md)，生产升级记录见 [docs/acceptance/V3.3.0升级记录.md](docs/acceptance/V3.3.0升级记录.md)，任务清单见 [docs/TASKS.md](docs/TASKS.md)。

## 文档

- [docs/SPEC.md](docs/SPEC.md)：完整的产品与开发规格
- [docs/TASKS.md](docs/TASKS.md)：任务拆分与进度
- [docs/open\_questions.md](docs/open_questions.md)：规格未写明的细节及其决定
- `docs/acceptance/`：生产服务器的安装与升级记录；`docs/ops/`：运维说明（安装、升级、备份与恢复）
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md)：AI 编程助手在本仓库中工作时遵守的规则
