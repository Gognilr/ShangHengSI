# V31-U5-06 中途收尾与续接

2026-11-04 · GPT-6.1 Sol · **用户因额度要求收尾，U5-06 未完成，不能进入 U5-07 验收。**

## 已完成的前序

- U5-04：九组实测、每模型六份质量抽查及恰好六次云端对照完成。用户确认 **7B Q6_K / 4 线程，接受人工核对的离线译稿定位，继续 05、06**（V31Q-100）。`24607b8` 登记模型和准入规则；完整证据见 V31-U5-04.md。
- U5-05：`02d367d`，解析 / 待确认期间可对话、可继续加文件；显式未确认文件报 FILE_NOT_CONFIRMED；A/B 用例核对只发 A、旧代号不变；日历词 golden。相关 526 通过、新用例修正夹具后另 1 通过，A-03 外发问题 0。见 V31-U5-05.md。

## U5-06 当前实现

新增 translator/main.py、Dockerfile、mt profile、固定源码与模型哈希；独立 translate 队列，原文按最多 800 字送内存盘，translator 同时处理一段，按 asr/pause 和 mt/pause 标记让路，空闲五分钟卸载。运行时仅容器内 HTTP 回环，不挂网络、密钥、原件或数据库。

新增迁移 0036 与 assistant_messages.local_result_enc，加密本地用户消息和译文，extras 仅留进度。后续云端历史排除加密消息，导出沿用既有 Word / 对照流程。新增 mt_enabled、状态检查和本地错误；翻译页默认本地（启用时），可切云端，只有云端要求确认脱敏。打包脚本、许可证、运维说明已补接。

**代码是中间检查点，尚未完成审核与最终验证。** 不能把核心测试或构建通过当作上线 / 功能验收。

## 已有验证与停止记录

证据目录：`E:\mt-work\u5-04-20261104\`。

- `u506-core-tests.log/.exit`：Docker 中 local translation、migrations、assistant chat/templates/export、local_models **78 通过，持久化 exit 0 已回读**。代表用例覆盖未确认原文、本地不调用网关 / 无 llm_calls、译稿加密、Word 导出、之后云端历史不含译文。
- `u506-frontend-build.log/.exit`：前端类型检查与构建通过，exit 0 已回读。此后仅修正 Translate.vue 的初始化变量顺序，仍须最终再构建。
- `u506-regression.log/.exit`：golden / A-03 / restore / sessions / local translation / retention / rotation / worker / frontend_style 长回归，用户要求收尾时停止，**exit 137，未通过完整门禁**；不可用前面的进度点推断全部通过。
- `u506-translator-build.log/.exit`：模型上下文传入、固定源码校验后编译中止；已终止本次 docker/buildx 进程树，exit 1，不是编译完成或镜像验收。
- Vite 已停止；Tabbit 仅创建空任务，错误的 --code 调用未执行，任务后续已过期，无页面、无浏览器验证。虚构用户 u506_browser 已在测试机禁用。
- 模型 / 源码仍在忽略目录 translator/models/mt/、translator/vendor/；不能加入 Git。源码归档来自此前获准下载的固定 llama.cpp 提交，未新加云端调用。

## 测试机中间状态

172.16.11.252 是本轮用户明确授权的测试机；旧 V1.0 数据已按授权清理，新实例保留。入口仍为 https://172.16.11.252:8443/ ，证书页 http://172.16.11.252:8080/cert/ 。管理员凭据在仓库外文件，禁止打印或提交。

持久实例目录 `/data/apps/aireview-u5-main-20261104/deploy`，项目 `aireview-u5-main`。app / worker 已用 `aireview-backend:u506` 更新、迁移 0036 生效；更新单元 aireview-u506-update 正常完成，app healthy。其余四服务保留。此次 backend 镜像是以 u504-main 为底、COPY 当时 app 目录的测试增量镜像，不是正式发布包；**之后 retention 状态、worker 连接池等末尾修改尚未同步**。

部署目录新增 `u506.override.yml`，app / worker 分别绑 16-19 / 0-7；`.env` profile 为 asr,mt。translator 镜像尚未构建 / 启动；前端仍是上次 u504-main，不能在 8443 页面上宣称新翻译已可用。本地 frontend dev 已配置远端代理后停止，续接可设 VITE_DEV_PROXY=https://172.16.11.252:8443 再启动。

虚构验证会话 `25b5e35a-d849-4338-9b71-8a5292e241a8`，账号 u506_browser 已禁用；未发送翻译请求。需要浏览器验证时只恢复该虚构账号，不改管理员或日常模型默认值。测试密码仅为虚构夹具，不写入报告。

## 续接顺序

1. 读当前 CLAUDE.md、TASKS-V31、U5 设计 5.2、本报告和工作区状态；保留无关未跟踪 docs 与附件。无需重做 U5-04 或再问已确认的模型 / 测试机 / V1 清理决定；云端六次预算已用完，不能再调用或试连接。
2. 核对本地队列取消 / 重试 / 多段状态、让路范围（当前 worker 解析与 app 问题标记已接，其他脱敏 / 追问入口仍须检查）、模型空闲卸载、保留期与 `_enc` 自动轮换。把必要的实现口径记 V31Q-101（下一编号当前仍为 101）；不要扩大安全机制或测试矩阵。
3. 完成 translator 构建和一条真实断网冒烟；同步最终后端与新前端到同一测试实例，再测真实服务正常解析 → 不确认脱敏 → 本地翻译 → API / 译文 / 对照 Word，核对无 llm_calls。此项尚无真实推理证据。
4. 按改动重跑必要的核心 / 回归文件，长回归不能沿用 exit 137。检查停止测试后遗留的 aireview_test_* 临时库，只清理确认属于本次的库。无需整仓全量（在 U5-08）。
5. 前端最终构建、样式检查；按 frontend.md 自检，完成本地 / 云端切换、未启用 / 不可用、处理中 / 停止 / 失败 / 正常与导出流程的浏览器验证。使用 tabbit 技能时 CLI 程序经 stdin 提供，不能使用 --code。
6. 完成后写简短 U5-06 报告、更新任务为已完成并提交，明确交 Opus 做 U5-07；不要自动执行 Opus 任务。
