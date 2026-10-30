# 商衡SI · 企业安全脱敏与智能审查平台（V2.0）

完整开发规格在 docs/SPEC.md。开始任何工作前，先阅读与当前任务相关的章节。
本文件只列每次会话都必须遵守的要点；与 SPEC 冲突时，以 SPEC 为准。

## 项目一句话

局域网内使用的商务文件初审小工具：上传文件 → 本地解析与校验 → 自动脱敏 → 用户确认 → 云端大模型初审 → 本地还原并显示问题清单 → 追问 → 导出 Word 报告。

## 必须遵守

* 不得增加 SPEC 没有的菜单、页面或功能。顶部只有"智能审核""审核历史""智能助手"三个菜单（智能助手自 V2-D 起，V2Q-124），右上角"设置"只对系统管理员、业务管理员可见（SPEC 1.3）。
* 界面视觉以 docs/design/frontend.md（第二版，商衡SI 品牌主题）为准：十六进制颜色只写在 frontend/src/styles/theme.css，页面和组件只用 CSS 变量。
* 技术栈固定（SPEC 第 3 节），不得替换。不得引入 Redis、Celery、RabbitMQ、Elasticsearch、独立的向量数据库服务（向量检索只用 PostgreSQL 的 pgvector 扩展，V2-E）、PyMuPDF、Presidio、PaddleOCR / paddlepaddle（OCR 用 RapidOCR）、任何本地大模型。本地模型只允许 SPEC 第 3 节列出的实体识别模型、向量模型各一个，用 onnxruntime 在 CPU 上离线运行；更换或新增须经选型报告和我确认。实体识别模型已确认为 urchade/gliner_multi-v2.1（Apache-2.0，FP32 ONNX，文件与 SHA-256 见 docs/v2/design/V2-B.md 6.2）；向量模型已确认为 BAAI/bge-m3（MIT，由官方 ONNX 量化的 INT8 ONNX，只用稠密向量，文件与 SHA-256 见 docs/v2/reports/V2-E-04a.md 第六节）。后端镜像不得安装 torch、transformers、gliner、sentencepiece；模型文件不进仓库。LibreOffice 只允许在 converter 容器中使用（旧格式转换，Q-136），不得进入 backend 镜像；converter 容器不接任何网络，只通过内存盘 /spool 与 worker 交换文件。
* 只有两处可以访问外网：① 模型网关 backend/app/llm/gateway.py，依次执行：调用上限检查 → 出口复检 → 发送 → 记录 llm\_calls，任何模型调用不得绕过网关；② 法规同步 backend/app/lawsync/client.py（V2-E），只访问固定的国家法律法规数据库域名（flk.npc.gov.cn）及其全文文件存储域名（flkoss.obs-bj2.cucloud.cn），只下载公开的法规目录与全文，请求中不含任何任务数据，默认关闭，同步结果经业务管理员确认才生效。其他代码不得访问外网。
* 发给模型的只有脱敏后的正文文本与用户消息，以及本地检索得到的公开法条原文、经过脱敏的公司范本条款（V2-E）、经过脱敏的公司知识库片段与本人历史合同条款（V2-D），全部经过出口复检；智能助手的流式发送也在网关内；不发图片和原文件。外发策略为 local\_only 的任务绝不调用模型。
* 原件、对照表、节点原文等敏感字段用主密钥 AES-256-GCM 加密存储（SPEC 17.3）。
* 日志中不得出现原文、真实名称、金额、API 密钥。
* 所有任务相关接口校验 owner\_id，非本人一律返回 404。管理员也不能查看他人任务内容。
* 金额计算用 Decimal（ROUND\_HALF\_UP），不得用浮点数。
* 数据库结构变更只能通过 Alembic 迁移。
* 界面文字用简体中文；数据库时间存 UTC，界面按 Asia/Shanghai 显示。
* 遇到 SPEC 未写明的细节：选最简单、最安全的做法，并记录到 docs/open\_questions.md。
* 冲突时的优先级：安全 > 正确性 > 简洁 > 性能。
* **永不过度设计、过度测试**（用户 2026-10-30 定为长期规则）：这是局域网内单机使用的小工具，出口在网络层只放行模型地址，其他端口由用户关闭。精力放在主线：脱敏、还原、审核结果的正确性。
  * 设计和修复只做真正影响使用的部分，不为假想的攻击或运维事故加防线、开关、自检或"测试门"。
  * 测试只写少量代表性用例，不写穷举矩阵。脱敏识别类用例写成 backend/tests/fixtures/redaction_golden/ 的样本标注，不另写测试函数；还原和出口复检的问题要用 golden 样本加 A-03 暴露出来。
  * 不为部署脚本、容器加固、源码扫描、迁移降级、备份包防篡改、样式与措辞等加测试，这些在测试虚拟机上演练时验证。
  * 脱敏行为按设计有意改变时，可以修改对应的旧测试，在报告中列出改动和原因。
  * 报告写短；整仓全量只在批次末跑一次。

## 当前阶段：V2.0 已完成，下一阶段 V3.0（2026-10-30 起）

V1.0 标签 `v1.0.0`（011d417）；V2.0 于 2026-10-30 完成总审核（V2-Z-05）并合并规格（V2-Z-06），标签 `v2.0.0`。docs/SPEC.md 已是 V2.0 版，是唯一开发依据；docs/v2/ 下的设计、问题与报告作为细节和历史保留。

* V3.0 方向见 docs/proposals/ 下的 V3.0 规划初稿。V3.0 先由 Opus 细化（范围、批次、任务卡、规格变更的写法），经我确认后才开发；确认前不改代码。
* V3.0 之前先做修复批次 V2-F（脱敏减负与修复，完成后为 V2.1）：任务见 docs/v2/TASKS-V2.md 的 V2-F 一节与 docs/v2/design/V2-F.md，按 TASKS-V2 第一节执行，含 V2Q-180 零宽字符、V2Q-181 PDF 图文混排页；新的问题在 V3.0 细化确定新编号规则前，仍按 docs/v2/open\_questions.md 的 V2Q- 编号记录。
* 正式发布包与正式服务器安装延后到后续开发全部完成后再安排（V2Q-179）。正式服务器 172.16.11.252 上任何停服、删数据、升级或恢复操作先征得我同意；部署与演练只在另外准备的测试虚拟机上做。
* 版本号随版本及时更新：SPEC 头部、CLAUDE.md 标题、登录页页脚（frontend/src/views/Login.vue）写当前版本；系统状态中的版本号由发布包构建时的 APP\_VERSION 写入。
* 线上版本需要紧急修复时，在对应版本标签上开修复分支，修复后合并回 main。

## 开发方式（V1.0）

* 严格按 SPEC 第 22 节的里程碑 M1 → M8 推进。全部开发任务见 docs/TASKS.md，同一时间只执行一个任务，按编号顺序串行。
* 开始一个里程碑前，由 Opus 细化该里程碑的任务（Mx-00），等我确认后才能执行。已确认的任务可以直接开发，不必再等确认。
* 执行任务必须遵守 docs/TASKS.md 第二节的执行规则：在 main 上开发，只改任务的文件范围，不改 SPEC 和本文件。每个任务结束时更新任务状态，输出完成报告，并告诉我下一个任务的编号、模型和推理强度；下一个任务的模型和本会话相同时直接继续，不同时停下来等我开新会话。
* 每个里程碑结束时，跑完 SPEC 第 23 节对应的验收用例，向我汇报结果，确认后再进入下一个。
* 每完成一个可运行的小步骤就提交一次 git，提交说明写清做了什么。提交说明以任务编号开头，用英语或日语书写（仓库公开在 GitHub 上）；界面文字、文档和代码注释仍用简体中文。
* 测试样本只用虚构内容，放在 backend/tests/fixtures/；模型调用在测试中用模拟服务替代。

## 目录结构

见 SPEC 第 5 节。

## 常用命令

在仓库根目录执行。以下为 PowerShell 写法；bash 中把 `${PWD}` 换成 `$(pwd)`。本机 Python 是 3.11，后端的运行、测试、迁移一律在 Docker 中进行。

**运行后端测试**（改了代码不用重建镜像，代码通过挂载进入容器；只有改了依赖才需要重建）：

```powershell
# 构建测试镜像（首次，或 requirements*.txt / Dockerfile 变更后）
docker build -t aireview-backend-test --target test backend

# 启动测试用的 PostgreSQL（首次创建；之后用 docker start aireview-test-db）
# V2-E-04 起测试库必须带 pgvector：用自建的 aireview-db 镜像（旧的 postgres:16.15 测试库容器先 docker rm -f 再按此重建）
docker build -t aireview-db:dev deploy/db
docker network create aireview-test
docker run -d --name aireview-test-db --network aireview-test -e POSTGRES_PASSWORD=test aireview-db:dev

# 运行全部测试（数据库测试会自建、自删独立的临时库）
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```

**本地启动整套系统**（app 启动时自动执行 `alembic upgrade head`）：

```powershell
# 首次：构建运行镜像、生成开发用主密钥、准备 .env
docker build -t aireview-backend:dev --target runtime backend
docker run --rm -v "${PWD}/deploy/secrets:/out" aireview-backend:dev python -m app.cli gen-key --out /out/master_key
copy deploy\.env.example deploy\.env   # 然后填写 POSTGRES_PASSWORD；本机 80/443 被占用时把 HTTP_PORT/HTTPS_PORT 改为 8080/8443

docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f app worker
docker compose -f deploy/docker-compose.yml down        # 停止（保留数据卷）
```

**前端开发与检查**（本机 Node 24）：

```powershell
npm --prefix frontend ci --include=dev
npm --prefix frontend run build      # vue-tsc 类型检查 + 构建
npm --prefix frontend run dev        # http://localhost:5173，/api 代理到 https://localhost:8443（需先启动整套系统）
```

浏览器面板不接受 Caddy 内部 CA 的证书，用浏览器检查界面时走 http://localhost:5173（.claude/launch.json 中的 frontend-dev）。

主密钥不能覆盖：`gen-key` 发现目标文件已存在会拒绝执行。deploy/.env、deploy/secrets/、deploy/backup/ 都不进仓库。

**数据库迁移**：新增迁移只能写在 backend/app/db/alembic/versions/ 下，并保证 tests/test\_migrations.py 中“模型与迁移一致”的测试通过。

**旧格式转换服务 converter**（compose 整套启动时自动构建）：

```powershell
docker build -t aireview-converter:dev converter
# converter 自身的测试（只用标准库 unittest）
docker run --rm --network none -v "${PWD}/converter/tests:/srv/converter/tests:ro" -v "${PWD}/backend/tests/fixtures/legacy:/srv/fixtures:ro" aireview-converter:dev python -m unittest discover -s /srv/converter/tests
```

**整仓全量测试**（批次末集中测试与验收使用。test_scripts.py、test_frontend_style.py 需要看到 deploy/ 与 frontend/，只挂载 backend/ 时这两个文件失败）：

```powershell
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}:/srv" -w /srv/backend aireview-backend-test pytest -q
```

**HTTPS 根证书**（V2-Z，SPEC 20.4；在离线目录生成，root.key 由保管人取走，不放服务器）：

```powershell
docker run --rm -v "${PWD}/offline-ca:/out" aireview-backend:dev python -m app.cli gen-ca --ip <服务器IP> --out /out
```

安装后员工打开 `http://<服务器IP>/cert/`，按页面在下载文件夹核对指纹并导入当前用户的根证书。

**构建发布包**（在 Git Bash 中执行；脚本要求 git 工作区干净，有未跟踪文件时先 `git clone` 到仓库外的目录，在 clone 中执行）：

```bash
bash deploy/scripts/build_release.sh 1.0.1 --out ../release
```

产出 `aireview-{版本}.tar.gz`（四个镜像、compose、脚本、运维说明、SHA256SUMS，归档内属主为 root）。服务器上的解包、安装、升级、备份与恢复见 docs/ops/运维说明.md。

