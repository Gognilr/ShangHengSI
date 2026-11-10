# V31-Z 前全面代码审核

2026-11-08 · Opus 5.5 · 范围：main（ceeb5d0），后端、前端、converter / transcriber、部署脚本

方法：读代码为主，重点看脱敏外发主线、权限与归属、worker 与队列、部署与升级；在本机开发环境（compose 全套 + 模拟模型）上核对了容器内存、响应头和外发记录。没有跑整仓测试（留给 Z-01），没有逐行读 7000 行的脱敏识别规则（这部分由 golden 样本与 A-03 覆盖）。

结论：安全主线（网关唯一出口、出口复检、owner 404、主密钥加密、日志不带原文）实现扎实，没发现绕过。发现 1 处管理员能看到用户原话的泄露、3 处影响使用的缺陷、5 项容量风险，以及若干小改进。脱敏识别与还原另做了专项，见 [V31-Z-redaction-review.md](V31-Z-redaction-review.md)。

## 一、建议在 Z-01 之前修（影响安全目标或使用）

**状态（2026-11-08）：1～4 已修，见 V31Q-113。** 修法：① 外发记录对助手类会话只写用途；② worker 启动时立即处理中断任务，加 `stop_grace_period: 40s`；③ upgrade.sh 随版本替换 models.yaml、model-manifests/、licenses/，caddy/ 片段新增直接放入、有变化另存 .new 并提示；顺带发现发布包漏了 rotate_key.sh，已补上（运维说明的“主密钥更换”一节由 Z-03 补写）；④ 前端连不上服务器时显示“暂时连不上服务器”并每 5 秒重试（本机停 app 容器实测：出现提示，恢复后自动进入原页面）。相关测试：test_assistant_chat、test_gateway、test_jobs、test_worker、test_admin_data、test_scripts 通过（test_worker 中启动用例按新行为改了断言）；前端构建通过。“处理备注”加密未做。

### 1. 助手会话标题是用户第一句原话，明文入库并出现在外发记录里

* 现象：前端把第一句问题的前 20 字设为会话标题（[Assistant.vue:115](../../../frontend/src/views/Assistant.vue)；会议纪要用用户填的会议主题，[Meeting.vue:75](../../../frontend/src/views/Meeting.vue)）。网关写外发记录时把任务标题抄进 `llm_calls.task_title`（[gateway.py:148](../../../backend/app/llm/gateway.py)），设置 → 数据 → 外发记录的列表直接显示（[admin/data.py:259](../../../backend/app/admin/data.py)）。
* 实测：本机库里 assist 记录的 task_title 有“买卖合同的验收期一般约定多长？”这类原话。用户第一句若是“华为那份合同的违约金是多少”，持有“数据”权限的管理员就能看到未脱敏的公司名。
* 影响：违反 SPEC 17.2“管理员可以查看外发记录（脱敏后的内容），不能查看他人任务的原文”。这是全系统唯一一处未脱敏的用户原话进入管理员视野。
* 建议：外发记录对助手类会话不写标题，改写用途标签（“智能助手 / 会议纪要 / 翻译”）或会话首个文件名；侧栏仍显示原标题（只有本人可见）。审核任务的“主文件名”是 SPEC 15 节明定的，文件名也常含对方公司名，是否一并改为不显示请你定。
* 顺带：结果页“处理备注”（`findings.handling_note`）是用户自由填写的文字，明文存库，而同类的“补充说明”是加密的（`user_note_enc`）。只有本人可见，优先级低，可一并加密。

### 2. worker 重启后，进行中的审核、回复、转写要卡 15 分钟

* 现象：worker 启动时不回收上次留下的 running 任务，只靠心跳超时（15 分钟，[jobs.py:18](../../../backend/app/db/jobs.py)）重新入队；compose 也没给 worker 设 `stop_grace_period`，Docker 默认 10 秒就强杀，代码里等 30 秒收尾的逻辑用不上。
* 影响：每次 upgrade.sh、rotate_key.sh、restore.sh 或 `docker compose restart` 之后：审核结果页停在“审核中”；助手会话里那条回复停在生成中，`availability()` 返回“上一条回复尚未完成”，点“停止”也只是打标记，**整个会话 15 分钟内不能再提问**；录音停在“转写中”。Z-03 的升级、换主密钥、恢复演练都会碰到。
* 建议：只有一个 worker 容器，启动时把全部 running 的任务立即按现有规则处理（未用完次数的回到 queued，助手回复照旧记为中断）；worker 加 `stop_grace_period: 40s`。约 10 行。

### 3. upgrade.sh 不更新模型清单

* 现象：发布包带 `models.yaml`、`model-manifests/asr.sha256`、`mt.sha256` 和 `caddy/` 片段（[build_release.sh:61-72](../../../deploy/scripts/build_release.sh)），这些都挂进 app / worker / web；但 [upgrade.sh:45](../../../deploy/scripts/upgrade.sh) 只复制 compose、VERSION、.env.example、scripts、docs。
* 影响：以后任何一次换模型或改清单的升级，app 和 worker 仍读旧清单：系统状态与 model-check 报不一致；转写结果的 `parser_version` 也按旧清单哈希计算（[transcribe.py:111](../../../backend/app/parsing/transcribe.py)）。3.1 是全新安装不受影响，Z-03 的 rc1 → rc2 演练只要两版清单相同就发现不了。
* 建议：upgrade.sh 一并复制 `models.yaml` 与 `model-manifests/`（这两项不允许现场改）；`caddy/` 片段按运维说明第 11 节是人工比较，脚本应像 Caddyfile 那样检测差异并提示，不然很容易漏。

### 4. 打开页面时后端暂时不可用，整页空白

* 现象：路由守卫首次导航调用 `auth.refresh()`（[router/index.ts:89](../../../frontend/src/router/index.ts)）；`/auth/me` 返回 502 / 503 或断网时 refresh 直接抛出（[auth.ts:45](../../../frontend/src/stores/auth.ts)），首次导航中止，没有 `router.onError`，App 只渲染空的 router-view。
* 影响：app 容器启动要做迁移并加载模型（三四十秒），升级、重启、早上服务器刚开机时，员工从开始菜单打开“已安装的应用”看到白屏，没有任何提示，只能自己刷新。
* 建议：守卫里非 401 的错误改为显示“服务器暂不可用，正在重试…”并每 5 秒自动重试，恢复后进入原页面。

## 二、容量与稳定性（建议修，或至少纳入 Z-03 实测）

**状态（2026-11-08）：二、三、四部分已按 V31Q-114 处理**（用户要求上线前全部修完）：5 app 进程数默认 1（`APP_WORKERS` 可调）；6 待转写 PCM 只为即将开始的转写准备，排队时保留加密原件；7 converter 先旧格式后录音、排队录音 25 分钟内不清理，旧格式等待改 300 秒；8 db max_connections=200；9 实测改一句重算 0.3 秒（本机 16 核、不含数据库，实体识别缓存命中），不改，Z-03 在测试机复核；10 网关对任何 local_only 任务一律拒绝；11 Caddy 压缩与安全响应头（已 caddy validate）；12 导出用加固解析器；13 助手移除文件时删转换结果、实体识别缓存与条款向量；14 `MT_CPUSET` 默认不设；15 每日上限的说明写明“按调用次数”，日界随 TZ；16 路由懒加载，主包 1.47 MB → 0.91 MB（gzip 290 KB），升级后取不到旧分块时整页重载；处理备注加密（迁移 0039）；18 误建目录已删。17：用户 2026-11-08 按审核结论确认（V31Q-115）。第 1 项中审核任务的主文件名也不再写进外发记录（V31Q-115）。

### 5. app 两个进程各加载一份本地模型，空闲就占 4.9 GiB

* 实测：本机 `docker stats` 空闲时 app 4.86 GiB、worker 2.38 GiB。原因是 `uvicorn --workers 2`（[docker-compose.yml:68](../../../deploy/docker-compose.yml)），每个进程都加载实体识别与向量模型。
* 影响：Z-03 第二轮 16 GiB 下，app 4.9 + worker 2.4 + transcriber 4 + translator 默认 12 已超过 16 GiB。另外确认页“一次决定约 1 秒”依赖的分段缓存在两个进程各一份，请求落到另一个进程时是冷缓存。
* 建议：Z-03 对比 `--workers 1` 的内存、确认页决定耗时与并发响应；若单进程够用，改为 1。

### 6. 待转写的录音 PCM 堆在 2 GiB 内存盘里

* 现象：解析任务一上来就把录音解码成 16 kHz PCM 放进 `/spool/asr/pending/`（[pipeline.py:209](../../../backend/app/tasks/pipeline.py)），转写并发只有 1。3 小时录音的 PCM 约 345 MB，排队期间一直占着内存盘；上传临时文件（TMPDIR=/spool，单个最大 500 MB）、旧格式转换也用这块盘（[docker-compose.yml:240](../../../deploy/docker-compose.yml)，2 GiB）。
* 影响：同时有四五个长会议排队、再有人上传录音时，内存盘写满，上传、.doc 转换、转写一起失败，提示也看不出原因。Z-03 只测一个 3 小时录音。
* 建议：解析时只生成回放副本与时长，PCM 改到轮到转写时再解码（`handle_transcribe` 已有“PCM 不在就解码”的分支）；或加入录音前检查内存盘余量并给出明确提示。

### 7. converter 单线程：长录音解码期间，别人的旧格式文件会转换超时

* 现象：converter 按到达顺序一个一个处理（[converter/main.py:113](../../../converter/main.py)），录音解码要跑两遍 ffmpeg（wav 与 opus），3 小时录音的上限是 20 分钟；worker 等旧格式转换最多 120 秒且含排队（[legacy.py:23](../../../backend/app/parsing/legacy.py)）。另外 converter 会删除 in/ 里超过 10 分钟没被领取的文件，第二个长录音排在第一个后面时输入会被删，worker 要等到 22 分钟才报“解码超时”。
* 建议：`pick()` 先取 doc / xls / rtf，再取录音；陈旧判断不删正在排队的录音。Z-03 繁忙场景顺带量一次 3 小时录音的解码耗时。

### 8. 高并发审核可能耗尽数据库连接

* 现象：worker 连接池 = 审核并发 ×（1 + 3 段）+ 其他 + 3 = 42，溢出再 42（[worker.py:322](../../../backend/app/worker.py)）；app 两个进程各 15。PostgreSQL 用默认 `max_connections=100`（[deploy/db/Dockerfile](../../../deploy/db/Dockerfile)）。每个分段线程在等模型回复的几分钟里一直占着一个事务中的连接，外发记录和进度各再开一个。
* 影响：8 份长文档同时审核时，worker 可接近 90 个连接，加上 app 会出现 “too many clients”，表现为随机 500 或审核失败。Z-03 繁忙场景没有多份并发审核。
* 建议：compose 给 db 加 `-c max_connections=200`（一行）；分段线程在调用网关前提交释放连接。

### 9. 逐字稿每改一句都全量重算脱敏

* 现象：改一句文字就 `engine.redact_task()` 整个会话（[meeting.py:198](../../../backend/app/assistant/meeting.py)）。V31Q-99 测得万字完整脱敏约 25 秒（无缓存），3 小时逐字稿约 4 万字；缓存能省多少没有实测。
* 建议：Z-03 在测试机上量一次“3 小时逐字稿改一句”的等待时间；超过 3 秒再定是否只重算改动的句子。

## 三、小改进与纵深防御（低优先，顺手可改）

| # | 问题 | 位置 | 建议 |
|-|-|-|-|
| 10 | local_only 只在网关的 assist 用途里拦截，审核与追问靠调用方检查 | [gateway.py:229](../../../backend/app/llm/gateway.py) | 网关对任何带 task_id 且 local_only 的调用一律拒绝，三行，符合“网关是唯一关口” |
| 11 | Caddy 不压缩、没有安全响应头：主 JS 1.36 MB 原样传输（本机实测），无 nosniff、防嵌入 | [Caddyfile](../../../deploy/Caddyfile) | 加 `encode zstd gzip` 与 `X-Content-Type-Options`、`frame-ancestors 'none'`、`Referrer-Policy` |
| 12 | 导出批注版 / 修订版用 lxml 默认解析器，与解析时的加固解析器不一致 | [annotated_docx.py:39](../../../backend/app/export/annotated_docx.py)、[revised_docx.py:48](../../../backend/app/export/revised_docx.py) | 复用 parsing/docx.py 的解析器设置 |
| 13 | 助手移除 .doc 等旧格式文件时不删转换结果，留下孤立的加密文件，会话删除时也删不到 | [assistant/service.py:506](../../../backend/app/assistant/service.py) | 同审核任务的 `delete_file`，删除 `converted_storage_key` |
| 14 | `MT_CPUSET` 默认 `8-15`，CPU 少于 16 核的机器启用 mt 时 translator 起不来 | [docker-compose.yml:220](../../../deploy/docker-compose.yml) | 默认留空，运维说明按核数给值 |
| 15 | 个人每日上限按调用次数计，一份长文档分段加格式重试会用掉好几次，用户难以理解 | [llm/usage.py](../../../backend/app/llm/usage.py) | 设置页说明“按调用次数，长文档一次审核可能计多次”，或改按操作计（需你定） |
| 16 | 前端所有页面静态引入、Element Plus 全量样式，主包 1.47 MB、CSS 600 KB | [router/index.ts](../../../frontend/src/router/index.ts) | 设置页等改为路由懒加载；配合第 11 条压缩，最低配置电脑首屏更快 |

## 四、流程事项

17. **17 条 V31Q 仍是“待确认”**：V31Q-46、47、50、57～63、65、75～79、99。多数 Opus 已审核“同意”，但没有你的确认。Z-04 按计划会汇总，建议届时一次性确认或关闭，不要带进 3.1。
18. 仓库根目录有误建的空目录 `backend;C`、`transcriber;C` 和 `backend/backend/.pytest_cache`（未跟踪，不影响构建），可删除。

## 五、看过、没发现问题的部分

* 归属：全部任务、轮次、问题、文件、会话接口都经 `owned_*` 依赖，非本人 404；全局搜索只查本人；管理接口按权限点 403。
* 外网：后端只有 `llm/gateway.py` 与 `lawsync/client.py` 用 httpx，均 `trust_env=False`、不跟随跳转；converter、transcriber、translator 均 `network_mode: none`。
* 网关：上限 → 复检 → 发送 → 独立会话记录的顺序在三个入口一致；附加参数不能替换 messages；密钥从记录中抹掉。
* 加密：AES-256-GCM 每次随机 nonce，录音分块流带块号与末块认证，防截断与拼接。
* 上传：扩展名与内容核对、宏与加密文件拒绝、解压大小与条目数上限、存储键防路径穿越。
* 登录：argon2id、5 次锁定、用户名不存在时同耗时、CSRF 挂在总路由、Cookie HttpOnly + Secure + SameSite。
* 前端没有 `v-html`；导出 xlsx 的文字单元格强制为文本，不会变成公式。

## 六、建议的安排

* 第一部分 1～4：都是小改动，建议 Z-01 开始前修完（或作为 Z-01 的“改错”项），改 1、2 后跑 golden、A-03 与相关测试。
* 第二部分 5～9：5、8 各一行配置，建议一并改；6、7 改动不大；9 先量再定。都写进 Z-03 的实测清单。
* 第三部分：10、11、13、14 顺手改；12、15、16 视情况。
