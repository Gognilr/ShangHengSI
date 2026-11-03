# V31-M 测试记录

## M-02 本地模型统一接入（2026-11-03）

已实现 models.yaml 八项、统一 --models、来源/复制后校验与按清单断网冒烟调度、发布清单和许可目录、worker 线程环境变量、compose 的 asr 骨架、model-check 与系统状态 local_models、运维说明第 12 节。重型组只读 MANIFEST 与心跳，不在 app / worker 加载。

验证：Docker Python 3.12、aireview-db:dev（带 pgvector）。五个相关文件首轮 78 通过、13 失败、3 跳过；13 个失败来自新模块目录层级错误与脚本 CRLF，已修复。受影响三文件复跑 54 通过。首轮已通过的 NER / 向量相关测试未重复。三个跳过是需 NER_MODEL_EVAL=1 的既有模型选型用例。补齐打包时的许可证正文存在检查后，原发布三场景再次 3 通过。compose config --quiet、bash -n 通过。实际断网 model-check 返回 0：NER / 向量实际 SHA-256 通过，OCR 锁定包与三模型通过，五项转写均为 manifest_only / disabled。

仅新增一个代表性检查用例，覆盖未启用、启用但无心跳、新鲜心跳与过期心跳，验证重型组未进入轻量加载路径。旧发布测试按 --models 目录形状适配，仍验证仓库清单优先与损坏来源在构建前失败。

边界：未跑整仓全量、未构建正式发布包（按卡留 M-07）；转写 Dockerfile / 主程序 / --smoke 在 M-04 接入。未调用云端真实模型、未下载模型、未连接正式服务器。测试与检查容器均使用 --rm，完成后已移除；既有测试库与其他项目容器保留。原有未跟踪资料未修改、未暂存。

V31Q-75 记录清单格式、重型校验口径、缓存与冒烟入口。用户已同意仅下载官方许可证小文本，八项 LICENSE 已齐，标点底座另附 LERT-LICENSE。bge-m3、GLiNER 等固定权重仓库未提供独立 LICENSE，采用对应官方项目原文，来源区别、下载日期、SHA-256 与 NOTICE 404 记录见 deploy/licenses/models/README.md、SOURCES.json；未自行补写版权行。M-02 已完成。此次收尾只修改许可正文与文档，不重复运行已通过的代码测试。

## M-03 录音上传、加密保存与解码（2026-11-03，已完成）

已实现迁移 0034（含 M-04～06 所需字段）、助手专用六类录音检查、默认 500 MB / 单次 1 个限制、asr 未启用拒绝、4 MiB AES-GCM 流式保存、converter 音轨解码协议、加密回放副本与原件删除。会话返回 media，解码成功先停在 queued / parsing，转写任务由 M-04 接入。保留期、任务/文件删除、备份恢复核对与主密钥更换已适配；回放副本按原件层处理。V31Q-76 记表名、上传内存盘、PCM 交接与删除标记口径。

Docker Python 3.12 + aireview-db:dev：crypto / migrations / assistant_sessions / retention 首轮 71 通过、1 失败（新增测试误用 error.code，当前统一响应为 code，已修正）；随后录音代表流程、rotate_key / backup_restore / upload_checks / legacy 164 通过。代表流程使用虚构 5 秒 WAV 和模拟 converter JSON，验证未启用拒绝、坏文件头拒绝、上传分块加密、PCM、加密回放和原件删除。分块用例验证篡改与截断拒绝。旧 converter 镜像挂载当前代码，协议、心跳与真实 LibreOffice 回归 21 通过。最终新增录音流程与分块密文轮换复核 2 通过，重复轮换跳过已完成流密文。compose config --quiet 通过。

用户已同意下载 Debian ffmpeg 与依赖，`aireview-converter:m03` 构建成功（ffmpeg 锁定 `7:7.1.5-0+deb13u1`；来源：[Debian 官方包](https://packages.debian.org/trixie/amd64/ffmpeg)）。新镜像在断网、只读、非 root、去能力条件下运行 converter 全部用例，22 通过（16.606 秒）。新增真实用例用 lavfi sine 生成 5 秒 WAV，验证解码结果时长 5000 ms、PCM 为 16 kHz / 单声道 / 16 位、回放副本为 Ogg Opus、输入和工作目录已清理；旧格式转换回归通过。上传、数据库与加密保存由前述模拟协议用例验证；本次不声称已走通 M-04 的真实转写或整套会议纪要流程。

运维说明已补齐 ffmpeg 版本、镜像内许可路径、Debian 源码来源、1 GiB 内存盘与录音保留方式。未跑整仓全量、未调用真实模型、未连接正式服务器；本次测试容器使用 --rm，退出后已移除。M-03 已完成，下一任务 V31-M-04（GPT-6.1 Sol，high）。
