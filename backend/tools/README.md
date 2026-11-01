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

## V31-R-02 回程体检

使用专用库 `aireview_eval_r02`、专用卷 `aireview-eval-r02-data`，不复用候选应用库或主密钥。
先在 Docker 中运行 `python tests/fixtures/return_probe/make_samples.py` 生成六份虚构 DOCX；中文、英文各三份，第三份各含报价明细表。

三个评测进程依次运行，其他配置一致：A 不加开关，B 加 `--copy-rule`，C 加 `--copy-rule --bracket-codes`。
两个开关仅用 `unittest.mock.patch` 临时替换提示词第 2 条、代号格式、分配器与 `outbound.OUR_CODE` 绑定，退出即恢复；产品代码与库中全局配置不改。
`--bracket-codes` 单独使用时只改变代号和示例，不隐含 `--copy-rule`。

各组加 `--return-probe` 后，只输出数字与最多十个代号写法，另建同名 `.json` 保存数字、样本摘要、任务和轮次 ID，便于合并首份试跑和剩余样本。
JSON 每完成一份即刷新，不含问题正文和已还原文字。报告与 JSON 都拒绝覆盖。常规模式仍输出原有详细报告。
回程统计直接读取 `findings` 原始字段（title、quote_model、problem、impact、suggestion、suggested_text、basis）及 `review_runs.doc_summary`，使用本任务对照表调用 R-01 `check()`；不对已经还原的 `run_detail` 再统计。
不传可读位置表，位置标记计入 note。字段分为建议条文与其他；分中文、英文、合计列出出现数、自动修正率、未能还原率、unknown、比例删除、note 与 shared 数。
出现数严格按设计为 `restored + 全部标记数`，包括 shared/note；分母为零记 N/A。
引用核实率为 `verified / 全部问题数`（包含缺失型）；结构校验失败计每次 `OutputError`，涉及样本数每份至多计一。
调用计数取本轮网关记录的 sent/error/blocked；底层网络重试仍按网关既有规则，结构重试会单独产生记录。模型调用与失败证据保存在专用库，报告不输出模型正文。

任务卡要求由用户本人核对候选卡片并录入密钥。2026-11-01 只读核对结果为 `https://api.deepseek.com`、`deepseek-flash`，附加参数为 `{"thinking":{"type":"enabled"},"reasoning_effort":"high"}`。
以下容器已按上述专用环境建立时，用户在 PowerShell 执行（不把密钥写进历史或聊天）：

```powershell
$r02Secret = Read-Host 'DeepSeek API Key' -AsSecureString
$r02Credential = New-Object System.Management.Automation.PSCredential('unused', $r02Secret)
$r02Credential.GetNetworkCredential().Password | docker exec -i aireview-eval-r02-runner python -m app.cli set-model --base-url https://api.deepseek.com --model deepseek-flash --api-key-stdin
Remove-Variable r02Credential, r02Secret
```

附加参数由执行者在专用库配置为核对过的值，不复制候选库的密钥。先经网关做一次连接测试，然后 A 组首份试跑；成功后只跑 A 剩余五份，再跑 B/C 各六份，避免重复计算首份。首份可复制到卷内单独目录（如 `/eval/pilot`），剩余五份放 `/eval/a_remaining`。
容器中运行示例：

```powershell
docker exec aireview-eval-r02-runner python tools/eval_review.py /eval/pilot --username evaluator --return-probe --output /eval/a-pilot.md
docker exec aireview-eval-r02-runner python tools/eval_review.py /eval/a_remaining --username evaluator --return-probe --output /eval/a-remaining.md
docker exec aireview-eval-r02-runner python tools/eval_review.py tests/fixtures/return_probe --username evaluator --return-probe --copy-rule --output /eval/b.md
docker exec aireview-eval-r02-runner python tools/eval_review.py tests/fixtures/return_probe --username evaluator --return-probe --copy-rule --bracket-codes --output /eval/c.md
```

连接失败或模型名不符时停下交用户处理。按 `docs/v31/design/V31-R.md` 2.5 比较 B/A 与 C/B，不能用单份试跑替代三组结论。

## 输出和退出码

`--output` 为必填，使用 UTF-8 Markdown，父目录必须已存在，目标文件必须不存在。常规模式报告含每份样本的文件摘要、任务与轮次 ID、状态、模型和提示词/要求版本、四种结论的数量、逐条要求结论，以及问题的类别、严重程度、要求编号、位置、引用状态、引用、影响与建议等完整文字。未能解析、预检拦截、达到调用上限、模型调用/校验失败的样本也会记录状态和通用错误码，并继续下一份。`partial` 明确标记缺失结论，不算整份执行失败。

报告包含虚构样本的还原结果，仅保存在指定开发目录；控制台只输出完成数量或通用错误，不打印样本内容、名称、金额、附加参数或密钥。报告中的模型文字按纯文本转义。统计只供人工比较，不是准确率、召回率或里程碑验收结论。

- `0`：全部样本完成或部分完成，报告已写出。
- `1`：至少一份样本失败或拦截，报告已写出。
- `2`：参数、环境、用户、目录或输出文件配置错误，或评测未完成；不能作为有效评测报告使用。

自动化验证（Docker 中，模型只使用 `httpx.MockTransport`）：

```powershell
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```
