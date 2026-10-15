# 企业商务文件AI智能审查助手（V1.0）

完整开发规格在 docs/SPEC.md。开始任何工作前，先阅读与当前任务相关的章节。
本文件只列每次会话都必须遵守的要点；与 SPEC 冲突时，以 SPEC 为准。

## 项目一句话

局域网内使用的商务文件初审小工具：上传文件 → 本地解析与校验 → 自动脱敏 → 用户确认 → 云端大模型初审 → 本地还原并显示问题清单 → 追问 → 导出 Word 报告。

## 必须遵守

* 不得增加 SPEC 没有的菜单、页面或功能。顶部只有"智能审核""审核历史"两个菜单，右上角"设置"仅管理员可见。
* 技术栈固定（SPEC 第 3 节），不得替换。不得引入 Redis、Celery、RabbitMQ、Elasticsearch、向量数据库、LibreOffice、PyMuPDF、Presidio、PaddleOCR / paddlepaddle（OCR 用 RapidOCR）、任何本地大模型。
* 只有 backend/app/llm/gateway.py 可以访问外网。网关依次执行：调用上限检查 → 出口复检 → 发送 → 记录 llm\_calls。任何代码不得绕过网关。
* 发给模型的只有脱敏后的正文文本，不发图片和原文件。外发策略为 local\_only 的任务绝不调用模型。
* 原件、对照表、节点原文等敏感字段用主密钥 AES-256-GCM 加密存储（SPEC 17.3）。
* 日志中不得出现原文、真实名称、金额、API 密钥。
* 所有任务相关接口校验 owner\_id，非本人一律返回 404。管理员也不能查看他人任务内容。
* 金额计算用 Decimal（ROUND\_HALF\_UP），不得用浮点数。
* 数据库结构变更只能通过 Alembic 迁移。
* 界面文字用简体中文；数据库时间存 UTC，界面按 Asia/Shanghai 显示。
* 遇到 SPEC 未写明的细节：选最简单、最安全的做法，并记录到 docs/open\_questions.md。
* 冲突时的优先级：安全 > 正确性 > 简洁 > 性能。

## 开发方式

* 严格按 SPEC 第 22 节的里程碑 M1 → M8 推进。全部开发任务见 docs/TASKS.md，同一时间只执行一个任务，按编号顺序串行。
* 开始一个里程碑前，由 Opus 细化该里程碑的任务（Mx-00），等我确认后才能执行。已确认的任务可以直接开发，不必再等确认。
* 执行任务必须遵守 docs/TASKS.md 第二节的执行规则：在 main 上开发，只改任务的文件范围，不改 SPEC 和本文件。每个任务结束时更新任务状态，输出完成报告，并告诉我下一个任务的编号、模型和推理强度；下一个任务的模型和本会话相同时直接继续，不同时停下来等我开新会话。
* 每个里程碑结束时，跑完 SPEC 第 23 节对应的验收用例，向我汇报结果，确认后再进入下一个。
* 每完成一个可运行的小步骤就提交一次 git，提交说明写清做了什么。
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
docker network create aireview-test
docker run -d --name aireview-test-db --network aireview-test -e POSTGRES_PASSWORD=test postgres:16.15

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

（构建与导出离线镜像的命令在 M8 补充。）

