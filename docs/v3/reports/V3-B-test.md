# V3-B 测试记录

## V3-B-01（2026-10-30）

- 测试先行：新增迁移与多模型代表用例，先观察到 2 failed、3 errors（缺表与模型），再实施。
- 新增 `llm_models` 与 0025 迁移、历史调用及审核轮次归属；旧密文直接 base64 解码迁入。网关必须传 model_id，普通与流式调用均记录模型 ID，连接／结构化测试复用网关。月上限按模型，个人日上限合计，费用用 Decimal 按各模型单价分别四舍五入后相加；密钥轮换由元数据覆盖新表。
- 验证环境：Python 3.12 Docker，独立 `aireview-v3b-test-db`（本机已有 `aireview-db:dev`，含 pgvector），临时数据库由测试创建和删除。保留原 postgres 测试容器。全部模型响应由 MockTransport 提供，未调用真实模型、未下载文件、未连接正式服务器。
- 验证结果：范围内相关用例 **50 passed**（73.25 秒；`-k "not set_model"` 排除两项 CLI）；两项 CLI 单独运行 **2 failed**。一个既有 Starlette/httpx 弃用警告。只跑 `test_v3_models.py`、`test_gateway.py`、`test_gateway_stream.py`、`test_migrations.py`、`test_rotate_key.py`，未跑整仓全量。模型与迁移自动比对已通过；有／无旧配置升级和新密钥解密均已通过。
- 旧测试调整：网关测试从 settings 改为模型表，显式传 ID；停用返回 MODEL_UNAVAILABLE；单价、月上限来自模型。保留重试、复检、密钥遮蔽、上海时间边界和 SSE 用例；轮换测试改查 bytea 密钥。原 CLI 两项测试保留。
- 初次收尾阻塞（已解决）：独立运行原两项 `set-model` 测试，均因 `cli.py:88` 写入已删除的 model.base_url 而 KeyError（2 failed）。当时该文件不在 B-01 或 B-02 范围，停止并申请补充授权，保留失败用例。V3Q-10 记录范围缺口，V3Q-11 记录未保存测试的费用和降级语义。
- 本提交是 B-01 底层中间状态；审核／追问／助手及管理接口仍是 B-02 内容，当前应用不可作为本批已完成版本运行或部署。范围问题按下节批准方案处理后，B-01 已标记完成。

### V3Q-10 收尾

- 用户批准的决定由 Opus 提交 `5125871` 写入：CLI 参数不变，按模型名称新增／更新 llm_models，新建须有密钥；两个默认 ID 为空时补齐，已有默认保持。实现仅改 cli.py 与现有两项命令测试，保留去 BOM、可见 ASCII、非法地址／附加参数、密钥不进入输出及加密存储的检查。
- Docker 中仅重跑本次受影响的 `pytest -q tests/test_gateway.py --tb=short`：**25 passed**，9.45 秒，一个既有 Starlette/httpx 弃用警告；包含原来失败的两项 CLI 用例，无排除。其余相关文件沿用上述通过结果，未重复全量。
- V3-B-01 已完成，V3Q-10 阻塞解除；下一任务 V3-B-02（GPT-6.1 Sol，high），负责管理接口与业务调用方接入。
