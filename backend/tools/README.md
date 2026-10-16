# 开发期审核效果评测

`eval_review.py` 对目录第一层的 DOCX 按文件名排序逐份评测，每份文件是一个独立任务、没有附件。只使用虚构样本。PDF、XLSX 和图片的评测留到支持这些格式后；非 DOCX 文件忽略，目录没有 DOCX 时拒绝运行。

脚本复用上传检查、原件加密保存、`parse_task`（解析与脱敏）、`create_run`（我方与出口预检）、`run_review`（网关与输出校验）、`run_detail`（还原）。全部模型请求仍由 `app/llm/gateway.py` 发出，调用上限、出口复检、调用记录与重试规则均生效。不提供绕过检查的选项。

## 环境和准备

只在 Docker 的 Python 3.12 环境执行，不用本机 Python。运行前必须满足：

- `APP_ENV=development` 显式设置；缺省或其他值均拒绝。
- `DATABASE_URL` 指向库名以 `aireview_eval_`（开发评测）或 `aireview_test_`（自动化测试）开头的专用库，迁移已到 head。不能连接生产库或正在联调的应用库。
- 专用库不运行 app/worker；脚本同步接管自己新建的 parse/review job。不要并发运行脚本或在运行中修改该库的设置。
- 已有启用的开发用户；`--username` 决定任务归属。脚本是开发工具，不提供产品接口，也不创建用户。
- `APP_MASTER_KEY_FILE` 和 `FILES_DIR` 指向专用开发数据目录；模型设置已通过现有 CLI 配置。

以下是从仓库根目录执行的 PowerShell 示例，使用本地模拟模型，不涉及真实厂商密钥。Docker 测试镜像按根目录 `CLAUDE.md` 构建。数据库、卷和模拟容器只在首次创建，不要覆盖已有主密钥。

```powershell
docker start aireview-test-db
docker exec aireview-test-db createdb -U postgres aireview_eval_dev
docker volume create aireview-eval-data

$evalDockerArgs = @(
  '--rm', '--network', 'aireview-test',
  '-e', 'APP_ENV=development',
  '-e', 'DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/aireview_eval_dev',
  '-e', 'APP_MASTER_KEY_FILE=/eval/master_key',
  '-e', 'FILES_DIR=/eval/files',
  '-v', "${PWD}/backend:/srv/backend",
  '-v', 'aireview-eval-data:/eval'
)
docker run @evalDockerArgs aireview-backend-test alembic upgrade head
docker run @evalDockerArgs aireview-backend-test python -m app.cli gen-key --out /eval/master_key
docker run @evalDockerArgs aireview-backend-test python -m app.cli create-admin --username evaluator

docker run -d --name aireview-eval-mock --network aireview-test -v "${PWD}/backend:/srv/backend" aireview-backend-test python tools/mock_llm_server.py
'mock-evaluation-only' | docker run -i @evalDockerArgs aireview-backend-test python -m app.cli set-model --base-url http://aireview-eval-mock:9000/v1 --model mock-model --api-key-stdin
```

示例中的标准输入文字仅是模拟服务使用的虚构凭据。真实模型的地址、模型名和密钥由用户在开发环境中自行配置，密钥不写命令行、不交给脚本，不提交到仓库。真实调用会产生厂商费用，脚本不预置任何厂商或模式参数。

## 运行与对比

可先把固定的虚构 DOCX 放到 `backend/tests/fixtures/` 下自选的样本目录。以下示例使用已有 DOCX 测试集，其中一些解析边界样本没有签约方或我方，预期会报告失败；需要全部完成时，应选择具有明确买卖双方的虚构商务文件。

```powershell
docker run @evalDockerArgs aireview-backend-test python tools/eval_review.py tests/fixtures/docx --username evaluator --stance sales --source outgoing --output /eval/review-baseline.md

# 进入相同环境的交互容器，在容器内传入 JSON，避免 PowerShell 5.1 原生命令的引号丢失。
docker run -it @evalDockerArgs aireview-backend-test sh
```

在上一步的容器 shell 中执行对比，完成后用 `exit` 返回 PowerShell。临时覆盖一层 JSON 键；thinking 的整个对象替换原对象，其他已有键保留：

```sh
python tools/eval_review.py tests/fixtures/docx --username evaluator --output /eval/review-thinking.md --extra-params '{"thinking":{"type":"enabled"}}'
```

`--extra-params` 必须是 JSON 对象，只对本进程的评测会话临时覆盖 `model.extra_params` 的读取结果，不写入数据库，不需要事后恢复，异常退出也不会留下全局设置。网关仍禁止用附加参数替换 `messages`。参数原值不写报告，只记录临时覆盖对象的 SHA-256；比较时应自行保存不含密钥的参数配置，并保持库内其他设置不变。

立场支持 `sales/purchase/other`，默认 `sales`；来源支持 `outgoing/incoming`，默认 `outgoing`。不自动指定我方：使用现有解析预选，恰好一方为我方才能审核；否则记录 `OUR_PARTY_REQUIRED`。每次运行都会创建新任务和轮次，保留原件、脱敏数据及调用证据，不自动删除；不要在评测过程中重置调用记录以绕过月上限。

## 输出和退出码

`--output` 为必填，使用 UTF-8 Markdown，父目录必须已存在，目标文件必须不存在。报告含每份样本的文件摘要、任务与轮次 ID、状态、模型和提示词/要求版本、四种结论的数量、逐条要求结论，以及问题的类别、严重程度、要求编号、位置、引用状态、引用、影响与建议等完整文字。未能解析、预检拦截、达到调用上限、模型调用/校验失败的样本也会记录状态和通用错误码，并继续下一份。`partial` 明确标记缺失结论，不算整份执行失败。

报告包含虚构样本的还原结果，仅保存在指定开发目录；控制台只输出完成数量或通用错误，不打印样本内容、名称、金额、附加参数或密钥。报告中的模型文字按纯文本转义。统计只供人工比较，不是准确率、召回率或里程碑验收结论。

- `0`：全部样本完成或部分完成，报告已写出。
- `1`：至少一份样本失败或拦截，报告已写出。
- `2`：参数、环境、用户、目录或输出文件配置错误，或评测未完成；不能作为有效评测报告使用。

自动化验证（Docker 中，模型只使用 `httpx.MockTransport`）：

```powershell
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```
