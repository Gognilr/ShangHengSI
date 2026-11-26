# 商衡SI · 企业安全脱敏与智能审查平台（V3.5.1）

系统目前的做法记录在 docs/SPEC.md，开始工作前先读相关章节。**SPEC 只是现状记录，不是永久规范**（用户 2026-11-04）：有更好的设计就直接提出并取代其中的条目，必要时整节删除，不得以“SPEC 这么写”为由照做别扭的设计或拒绝改进；改动后同步更新 SPEC。下列安全要点是产品目标本身，不在此列。

## 项目一句话

局域网内使用的商务文件初审小工具：上传文件 → 本地解析与校验 → 自动脱敏 → 用户确认 → 云端大模型初审 → 本地还原并显示问题清单 → 追问 → 导出 Word 报告。

## 必须遵守

* 新增侧栏菜单或页面先给出方案经我确认（防止功能膨胀，不是因为 SPEC 没写）。左侧侧栏菜单为：工作台｜文件审查（文件审核、合同对比、标书初审；合同对比 V3.5.1 入侧栏）｜工具（会议纪要、翻译）｜历史记录（V3.4 一事一入口，V34Q-01；另有新建会话、搜索、近期会话，SPEC 1.6、6.9）。“设置”在账号菜单中（V34Q-12），对所有人可见，第一个分页“个人”（外观、修改密码），管理分页按权限点显示（SPEC 1.3）。
* 界面视觉以 docs/design/frontend.md（澄明，V3.5 起）为准，标书初审见该规范 8.5。十六进制颜色只写在 frontend/src/styles/theme.css，页面和组件只用 CSS 变量。衡线标志资产在 docs/design/v35/brand/。
* 取舍外部提案与新能力按 SPEC 2A“技术路线”：确定性规则优先、其次本地 CPU 小模型、最后才是只见脱敏文本的云端模型；主线功能须在无 GPU 的基线机器上可用；新能力附代表性样本的效率与质量实测。
* 技术栈固定（SPEC 第 3 节），不得替换。不得引入 Redis、Celery、RabbitMQ、Elasticsearch、独立的向量数据库服务（向量检索只用 PostgreSQL 的 pgvector 扩展，V2-E）、PyMuPDF、Presidio、PaddleOCR / paddlepaddle（OCR 用 RapidOCR）、未经确认的本地大模型；本地大模型只允许 deploy/models.yaml 中经确认的翻译模型一个（Hy-MT2-7B Q6_K，llama.cpp CPU，V31Q-100）。本地模型只允许 SPEC 第 3 节列出的实体识别模型、向量模型各一个，以及转写一组（语音分段、转写、标点、说话人分段、声纹各一个，V31-M），用 onnxruntime（转写一组的说话人分离另用 sherpa-onnx）在 CPU 上离线运行；本地模型的唯一清单是 deploy/models.yaml（接入、放置与资源按 docs/v31/design/local-models.md），更换或新增须经选型报告和我确认（该规范第七节）。实体识别模型已确认为 urchade/gliner_multi-v2.1（Apache-2.0，FP32 ONNX，文件与 SHA-256 见 docs/v2/design/V2-B.md 6.2）；向量模型已确认为 BAAI/bge-m3（MIT，由官方 ONNX 量化的 INT8 ONNX，只用稠密向量，文件与 SHA-256 见 docs/v2/reports/V2-E-04a.md 第六节）。转写一组已确认为 FireRedVAD、FireRedASR2-AED INT8、FireRedPunc INT8、pyannote segmentation-3.0、3D-Speaker CAM++（Apache-2.0 / MIT，文件与 SHA-256 见 transcriber/models/asr/MANIFEST.sha256 与 docs/v31/reports/V31-M-01.md），只在 transcriber 容器中运行。任何镜像不得安装 torch、transformers、gliner、sentencepiece（导出 ONNX 只在仓库外的一次性容器中做）；模型文件不进仓库。LibreOffice 与 ffmpeg（录音解码，V31Q-71）只允许在 converter 容器中使用，不得进入 backend 镜像；converter、transcriber 与 translator 容器不接任何网络，只通过内存盘 /spool 与 worker 交换文件。
* 只有两处可以访问外网：① 模型网关 backend/app/llm/gateway.py，依次执行：调用上限检查 → 出口复检 → 发送 → 记录 llm\_calls，任何模型调用不得绕过网关；② 法规同步 backend/app/lawsync/client.py（V2-E），只访问固定的国家法律法规数据库域名（flk.npc.gov.cn）及其全文文件存储域名（flkoss.obs-bj2.cucloud.cn），只下载公开的法规目录与全文，请求中不含任何任务数据，默认关闭，同步结果经业务管理员确认才生效。其他代码不得访问外网。
* 发给模型的只有脱敏后的正文文本与用户消息，以及本地检索得到的公开法条原文、经过脱敏的公司范本条款（V2-E）、经过脱敏的公司知识库片段与本人历史合同条款（V2-D），全部经过出口复检；智能助手的流式发送也在网关内；不发图片、录音、声纹和原文件。外发策略为 local\_only 的任务绝不调用模型。
* 原件永远不改：批注版、修订版、报告等导出一律在服务器内存中生成新文件，服务器不保留导出文件；模型从不接收或产出文件（V3.0）。
* 原件、对照表、节点原文等敏感字段用主密钥 AES-256-GCM 加密存储（SPEC 17.3）。
* 日志中不得出现原文、真实名称、金额、API 密钥。
* 所有任务相关接口校验 owner\_id，非本人一律返回 404。管理员也不能查看他人任务内容。
* 金额计算用 Decimal（ROUND\_HALF\_UP），不得用浮点数。
* 数据库结构变更只能通过 Alembic 迁移。
* 界面文字用简体中文；数据库时间存 UTC，界面按 Asia/Shanghai 显示。
* 遇到 SPEC 未写明的细节：选体验最好且安全的做法，并记录到当前阶段的 open\_questions.md。
* 冲突时的优先级：安全 > 正确性 > 简洁 > 性能。
* **永不过度设计、过度测试**（用户 2026-10-30 定为长期规则）：这是局域网内单机使用的小工具，出口在网络层只放行模型地址，其他端口由用户关闭。精力放在主线：脱敏、还原、审核结果的正确性。
  * 设计和修复只做真正影响使用的部分，不为假想的攻击或运维事故加防线、开关、自检或"测试门"。
  * 本条针对测试、门禁、防线和报告，不用来删减已确认的功能（用户 2026-10-30 说明）。
  * 测试只写少量代表性用例，不写穷举矩阵。脱敏识别类用例写成 backend/tests/fixtures/redaction_golden/ 的样本标注，不另写测试函数；还原和出口复检的问题要用 golden 样本加 A-03 暴露出来。
  * 不为部署脚本、容器加固、源码扫描、迁移降级、备份包防篡改、样式与措辞等加测试，这些在测试虚拟机上演练时验证。
  * 脱敏行为按设计有意改变时，可以修改对应的旧测试，在报告中列出改动和原因。
  * 报告写短；整仓全量只在批次末跑一次。

## 当前阶段：V3.5.1 质量修复版候选待发布

V3.51 计划见 docs/v351/PLAN.md（Q-01～18），V351-Z 复核与上线方案见 docs/v351/reports/V351-Z.md。V3.5 计划见 docs/v35/PLAN.md。推送发布标签与升级服务器前先取得用户确认。下一阶段计划由 Opus 起草、经我确认后开工；新问题与报告放该阶段的目录。任务由 Claude 与 ChatGPT 共同分担，每个任务单独开一个新会话。真实合同与真实标书只在本机评测，原文、名称、金额、密码不进仓库、对话与日志（例外：V34Q-39 的两组过期公开招投标文件可在会话中通读分析，仍不进仓库）。

已发布：标签 `v1.0.0`、`v2.0.0`、`v2.1.0`、`v3.1.0`（含 V3）、`v3.2.1`（含 V3.2，2026-11-15，报告 docs/v321/reports/V321-Z.md）、`v3.3.0`（2026-11-15，报告 docs/v33/reports/V33-Z.md）、`v3.4.0`（2026-11-19，报告 docs/v34/reports/V34-Z.md）、`v3.5.0`（2026-11-25，报告 docs/v35/reports/V35-Z.md）。docs/SPEC.md 记录源码当前做法。docs/v2/～docs/v35/ 为历史，开工时不必读。

* 服务器 172.16.11.252：今后的正式服务器，现为 3.5.0（各次记录在 docs/acceptance/）。**我宣布正式启用之前没有生产数据，可以随意部署、测试、重装**（用户 2026-11-12）；宣布之后任何停服、删数据、升级或恢复操作先征得我同意。
* 版本号随版本及时更新：SPEC 头部、CLAUDE.md 标题、登录页页脚（frontend/src/views/Login.vue）写当前版本；系统状态中的版本号由发布包构建时的 APP\_VERSION 写入。
* 线上版本需要紧急修复时，在对应版本标签上开修复分支，修复后合并回 main。

## 开发方式

* 在 main 上串行，同一时间只执行一个任务，按当前阶段计划的顺序；计划由 Opus 起草、经我确认后才开工，已确认的任务直接开发。执行任务不改 SPEC 和本文件，建议的改动写进报告，由该阶段的 Z 任务合并。
* 任务结束时更新计划中的状态列，写短报告，告诉我下一个任务的编号与两侧模型。
* 每完成一个可运行的小步骤就提交一次 git，提交说明以任务编号开头，用英语或日语书写（仓库公开在 GitHub 上）；界面文字、文档和代码注释仍用简体中文。
* 测试样本只用虚构内容，放在 backend/tests/fixtures/；模型调用在测试中用模拟服务替代。

## 目录结构

见 SPEC 第 5 节。

## 常用命令

在仓库根目录执行。以下为 PowerShell 写法；bash 中把 `${PWD}` 换成 `$(pwd -W)`。本机 Python 是 3.11，后端的运行、测试、迁移一律在 Docker 中进行。在 Git Bash 中运行 docker 时须加 `MSYS_NO_PATHCONV=1`，挂载用 `$(pwd -W)`：否则 `-v "$(pwd)/backend:/srv/backend"` 在本仓库的中文路径下会挂到错误的目录、跑的是旧代码（V34-B-08c）。

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
bash deploy/scripts/build_release.sh 1.0.1 --models <离线模型目录> --out ../release
```

产出 `aireview-{版本}.tar.gz`（已启用的镜像、compose、脚本、运维说明、SHA256SUMS，归档内属主为 root）。服务器上的解包、安装、升级、备份与恢复见 docs/ops/运维说明.md。

