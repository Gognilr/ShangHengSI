# V2-C 集中测试报告

2026-10-26，GPT-6.1 Sol。C-07 完成提交为 `1739d33`。本报告对应 C-08；后台测试、构建、浏览器清单和指定机器隔离演练均已通过。V2Q-73 已由用户批准，记录人列补齐并完成构建及四尺寸浏览器核对，**C-08 已完成，停在 C-09，等待 Opus（high）整批审核。**

## 整仓全量与批准的旧测试同步

| 验证 | 结果 | 独立退出凭据 |
|-|-|-|
| 首轮整仓挂载、Docker Python 3.12 | 2840 passed、4 failed，1219.75 秒 | `.codex-tmp/V2-C-08-full.log/.exit`，1 |
| V2Q-72 批准的三个完整文件 | 34 passed，63.86 秒 | `V2-C-08-legacy-contracts.log/.exit`，0 |
| 同步后整仓全量重跑 | **2844 passed、1 warning，1126.55 秒** | **`V2-C-08-full-final.log/.exit`，0，已保存重读** |
| 前端 vue-tsc 与构建 | **通过**；既有大块体积提示 | `V2-C-08-build.log/.exit`，0，已保存重读 |
| V2Q-73 补列后的 vue-tsc 与构建 | **通过**；既有大块体积提示 | `V2-C-08-creator-build.log/.exit`，0，已保存重读 |
| 差异空白检查 | 通过 | `git diff --check` |

四项失败对应三个旧测试文件，用户逐项批准为 V2Q-72，已写入 questions/V2-C.md 与总索引：

- test_audit.py：旧的单字段保留请求改为合法五项（原件 30，结果/外发/审计/未完成均 0），继续检查审计覆盖和敏感输入不进入审计。
- test_eval_review.py：上限已在确认发送前拒绝，精确断言评测报告行 `| 错误码 | CAP\_REACHED |`；保留无模型请求、出口拦截、报告不含敏感输入等断言。
- test_user_note_redaction.py：合法五项保留配置、last_activity_at 回拨 31 天、完整六项清理计数；原文清除后仍能还原结果与报告的断言保留。

没有为这四项失败修改产品代码，没有删除或放宽设计用例、黄金样本或 A-03。全量唯一警告为既有 Starlette/httpx 弃用提醒。

```powershell
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}:/srv" -w /srv/backend aireview-backend-test:b06 pytest -q
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}:/srv" -w /srv/backend aireview-backend-test:b06 pytest -q tests/test_audit.py tests/test_eval_review.py tests/test_user_note_redaction.py
npm --prefix frontend run build
```

## 浏览器与真实接口

使用 Tabbit 技能、真实前端与后端、真实鉴权/CSRF/队列/网关，模型只指向隔离的模拟服务。本机独立 compose 项目 `aireview-v2c08`，数据库、原件卷、虚构密钥独立；前端 `http://localhost:15176`，只绑定本机的代理端口 18086。app、worker、mock、db 在内部网络，只有代理连接到单独前端网络。账号、合同、词条、审计行和统计样本均为虚构。

| 清单 | 已验证的行为与回执（`.codex-tmp/v2c08/browser-*.json`） |
|-|-|
| 管理员提醒 | alerts：只收起一条，其他提醒仍显示，智能审核/历史/设置间保持，刷新恢复；alert-hide-open：全部收起后 main 顶部为 64px，不留空白提醒区；alert-text-change：同一用户通过真实修改密码入口刷新 /auth/me，CALL_CAP_NEAR 从 1/1 变为 2/1 后显示，未变化的备份提醒仍保持隐藏，再刷新页面恢复；退出再登录也恢复。无持久化存储 |
| 五项保留与旧值 | data-bounded/data-1280：45 天旧值和改档提示；非法保存提示可见；retention-api-422：携当前登录态、CSRF 的真实接口返回 422 VALIDATION_ERROR；retention-save：改选 30 天、其他四项永久，PUT 200，刷新后 RETENTION_REVIEW 消失 |
| 存储面板 | 初始缺少 7 天样本时显示无法估算；data-stats-audit：加入虚构历史样本后显示占用、可用天数、备份盘剩余 30 GB 和统计时间。只验证接口值展示，虚构样本不作为容量预测或性能结论 |
| 审计 | audit-filter-page：选择登录动作，接口合计 31 条，两页 20/11，无重复且动作均为 login；audit-empty：恢复动作空结果 200/0；audit-error：浏览器拦截模拟 500 时显示中文错误，撤销拦截并刷新后恢复正常 |
| 日限额 | model-cap：系统管理员保存个人上限 1，PUT 200；普通用户首次审核 201/completed、导出 200；chat-cap 与 confirm-cap：追问、下一任务确认分别返回 409 USER_CAP_REACHED 并显示中文提示；local-api-verify：两次拒绝后总 sent 仍为 1、对话记录 0，第二任务保持 awaiting_confirm |
| 重复文件 | duplicate：409 DUPLICATE_FILE 返回本人旧任务，弹窗显示旧时间及“打开上次结果/继续新建”；点击继续后确实创建第二个不同 task_id，随后完成解析。继续新建回执中对 multipart postData 的读取返回 null，脚本附加断言失败，实际 POST 已 201；后续 confirm-cap/new-task-remembered 验证新任务真实存在并保持待确认，不把这条脚本异常当产品失败或重复创建 |
| 角色 | biz-login：仅审核要求/脱敏，只有词条冲突提醒，直接访问数据跳审核要求；sys-login：仅账号/模型/数据，备份与冲突提醒，访问脱敏跳账号；admin 可见全部分页；disable-conflict：业务管理员通过真实开关停用冲突词 200；普通用户没有设置入口和管理员提醒，直接访问设置跳智能审核 |
| 以后自动替换 | manual-remember：普通用户在真实原文中选择“煅后石油焦”，选择其他并勾选，以 remember=true 保存，POST 200；new-task-remembered：下一个真实上传任务外发文本已不含该原词；local-api-verify：custom 词条启用，created_by 为该普通用户。V2Q-73 补列后，creator-verify 核对业务管理员所见记录人为“虚构普通用户”，旧词条“验收”的空记录人显示“—”；四尺寸单元格均在视口内，页面无水平溢出 |
| 到期提示与历史 | expiry-layout：把虚构任务活动时间回拨到原件到期前 6 天，显示“原件将于 2026-11-01 删除，之后不能重新审核和追问”；history/history-layout：两条本人任务、保留至列显示永久（结果层为永久） |
| 四种宽度与焦点 | 1280×800、1366×768、1440×900、1920×1080；对已加载的账号、模型、数据、确认页、结果页、历史页检查 documentWidth 等于视口宽，检查到的控件不超出视口；history-layout：Tab 焦点 outline 为 solid；normal-console-layout：三种设置页正常导航没有 pageerror/console error |

截图已打开目视检查并复制保存，分别覆盖四种宽度；主要页面未见文字重叠或水平溢出，确认页粘底栏与结果页左右布局可用：

- `.codex-tmp/v2c08/evidence/data-1280.png`：旧版保留值与非法保存提示。
- `.codex-tmp/v2c08/evidence/result-1366.png`：到期提示、结果与原文。
- `.codex-tmp/v2c08/evidence/confirm-1440.png`：确认页、签约方、粘底栏。
- `.codex-tmp/v2c08/evidence/history-1920.png`：保留至列、两条记录及键盘焦点。
- `.codex-tmp/v2c08/evidence/custom-creator-1280.png`：V2Q-73 补列后，业务管理员查看普通用户创建的自定义词及空记录人；已目视检查。四尺寸断言见 `browser-creator-verify.json`。

几何回执为四尺寸逐页检查，截图为上述主要视图的目视证据，不声称所有页面所有状态均有四张截图。首次环境存在密钥属主、脚本 PYTHONPATH 和 internal 网络端口未发布导致的 502/入口空白，已修正隔离配置后完成真实 UI 流程；保留失败回执。另有一次截图超时、原生选择框文字遮挡 input 的自动化点击超时，后续改点可见文字并取得真实回执。预期 409、模拟 500、先前环境 502 与正常页面控制台结果分别记录。

## 指定机器隔离演练

用户在本轮指定 `172.16.11.252` 后，复用已保存的 SSH 密钥连接。该机五个既有 V1.0 正式容器继续运行，本轮只操作新建目录 `/data/aireview-v2c08-20261026` 与 compose 项目 `aireview-v2c08-target`，使用独立 internal 网络、数据库/原件卷、虚构密钥和模拟模型，**无宿主公开端口**。正式容器不停止、不升级、不恢复。

测试只使用已有离线镜像，未联网下载、未调用真实模型。镜像传输双方 SHA-256 相同：`b4cd5ef61daea0cefad5735f02424dea3ebfbb564344953c805cc7d9fa78373e`。

| 演练 | 结果与独立凭据（仓库 `.codex-tmp/`） |
|-|-|
| V1.0 升级：未保存原件保留期 | 从 V1.0 head 0006 的 schema 建库，加入旧用户/管理员、400 天旧任务和加密原件，再升级 head。五项均 0、review_pending=true；清理不删任务或原件。`V2-C-08-target-upgrade2.exit=0`，upgrade.json |
| V1.0 升级：已保存原件 30 天 | 同样从 0006 建库，先保存 original_days=30；升级后仍为 30，新增四项 0，提醒存在；清理只删该旧原件，任务保留，其他层计数为 0。同期 upgrade.json；0001–0006 的迁移链与 v1.0.0 相同，未用现版 ORM 建表冒充旧库 |
| 活动时间回拨后的清理 | 独立测试数据库与原件目录；原件/结果/未完成/外发正文/审计分别到期，另有新任务、解析中、审核中。计数 tasks=2、files=2、results=1、unfinished=1、outbound=1、audit=1；新/忙任务保留；外发 token、状态保留。`V2-C-08-target-retention-restore.exit=0`，retention.json |
| 8 路真实审核 | 真实 HTTP 上传、解析、主体确认、疑似决定后同时提交八个 review job，队列 running 峰值明确为 **8**，全部 completed，模拟模型每次延迟 4 秒；追问与导出正常，worker 未出现连接池超时。首次 `V2-C-08-target-flow-before.exit=0`；补充内存采样的 `V2-C-08-target-pressure.exit=0`，pressure.json |
| 内存 | 第二次 8 路运行随同逐秒采集四个测试容器 memory.current；观测到同一采样点合计峰值 **5,552,889,856 字节**，其中 app 3,517,620,224、worker 1,752,993,792、db 263,909,376、mock 18,366,464。此为 1 秒采样观测峰值，不是精确瞬时最大值；不外推吞吐或发布容量。原始 pressure-memory.tsv 和本机 pressure-memory-summary.json 保留 |
| 实际更换主密钥 | 调用本任务真实 rotate_key.sh：先备份成功，停服、rotate、verify、保存旧副本、原子换文件、启动；随后真实上传第九任务、审核、追问、导出均通过。`V2-C-08-target-rotate2.exit=0` |
| 旧备份 + 旧主密钥恢复 | 仅停止隔离 app/worker，离线换回测试旧密钥，执行真实 restore.sh；数据库从备份恢复，移出 9 个原件、恢复 8 个，核对 8 份原件和 8 条记录，清除隔离旧库/旧原件。登录后更换前八任务存在、更换后第九任务不存在，旧报告与追问正常。`V2-C-08-target-retention-restore.exit=0`，restored.json |
| 正式服务保护与清理 | 五正式容器的 Id、状态、StartedAt、RestartCount 与测试前完全一致，重启计数均 0。隔离容器、网络、卷、此次导入的测试镜像、传输目录均已清理。`V2-C-08-target-cleanup.exit=0`、`V2-C-08-production-final-unchanged.exit=0` |

本机证据归档 `.codex-tmp/v2c08/target-evidence.tar` 已校验与远端 SHA-256 一致：`347307e41b4a1e41781a4287280f44d5fc142e018a6b180d2b973bb5c7a19809`；`V2-C-08-evidence-hash-verify.exit=0`。解包到 `.codex-tmp/v2c08/target-final-evidence/`，未导出任何正式数据或密钥。

外部演练脚本曾因初始化卷权限、Python 命令引用、一个旧字段名称、跨文件系统 rename 等问题失败，均只修复仓库外/未跟踪的测试脚本并保留原失败日志。第二次压力运行仅为补齐与八并发同时发生的内存采样，不把更换/重启之后的 memory.peak 冒充原八并发峰值。历史外层失败未覆盖或改写为 0。

## 遗留与下一步

V2Q-72 的三文件范围批准和 V2Q-73 的单文件显示扩展均已落实，批准记录见 questions/V2-C.md。最后的产品变更仅为 Redaction.vue 增加记录人列，没有后端改动；沿用此前 2844 项整仓通过凭据，另完成补列后的前端构建和浏览器验证。Tabbit 任务正常结束，finished=true、forcedCleanup=false，截图与回执已保留。

C-08 已完成，下一步为 C-09（Opus，high）整批审核，本会话停在此处。本机虚构数据的 `aireview-v2c08` 与 http://localhost:15176 测试页保留供审核复核；指定机器上的隔离资源已清理，其余既有本机项目未改动。
