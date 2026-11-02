# 本地模型接入规范

2026-11-02 · Opus 5.5 起草 · 用户确认写规范、在 V31-M 落实（V31Q-49）

适用于本系统在服务器上离线运行的全部本地模型：现有实体识别（GLiNER）、向量（bge-m3）、OCR（RapidOCR），V31-M 的转写一组（语音分段、转写、标点、说话人分段、声纹），以及以后经确认的本地翻译（V31Q-48）。云端模型走模型网关（SPEC 11），不在本规范范围内。

目标：换一台服务器时，照运维说明的一章就能装好、核对、按硬件调好；以后再加模型，照第七节的固定步骤，不再每次临时安排。

## 〇、现状与缺口（2026-11-02 核对）

已经做到（保留不变）：模型只在开发机离线获取、固定版本；仓库只存 `MANIFEST.sha256`，模型文件不进 Git；构建发布包时以仓库清单校验后复制进构建上下文，Dockerfile 再校验一次；worker 启动时再校验，不通过拒绝启动；四镜像离线包交付，服务器运行时从不下载。

缺口：

1. 每个模型单独处理：build_release.sh 为 GLiNER、bge-m3 各写一段、各要一个参数（`--ner-models`、`--embedding-models`）；RapidOCR 跟随 pip 包，另一种校验方式。
2. 没有一份“本机有哪些模型”的清单；来源、提交、许可证散在 SPEC 第 3 节、V2-B.md 6.2、V2-E-04a.md 第六节。
3. 线程数写死在代码中（ner.py、embedding/engine.py 的 `_threads = 8`），按 28 vCPU 定，换机器只能改代码。
4. 运维说明没有“本地模型”一章，没写 `--ner-models`，开头硬件仍是 V1.0 的“10 核、15 GiB”。
5. 发布包没有附带模型的许可证文本（Apache-2.0、MIT 均要求随分发附带）。

## 一、模型清单 `deploy/models.yaml`

全系统唯一的模型清单，构建脚本、运维说明和系统状态页都以它为准。SHA-256 只写在各模型目录的 `MANIFEST.sha256` 中（运行时校验读它），清单只引用、不重复写哈希。

```yaml
# 每个模型一项；分组模型（如转写一组）每个模型各一项，group 相同
- id: ner                          # 目录名、构建参数子目录名
  purpose: 脱敏实体识别（SPEC 9.3 第 8 步）
  image: backend                   # 所在镜像：backend / transcriber / translator
  dir: backend/models/ner          # 仓库内目录（只含 MANIFEST.sha256）
  source: huggingface.co/urchade/gliner_multi-v2.1@443d26d654e0324125a96bebd8e796c14ff2efe6
  convert: 仓库外导出 FP32 ONNX，见 docs/v2/design/V2-B.md 6.2
  license: Apache-2.0              # 许可证文本放在 deploy/licenses/models/ner/
  runtime: onnxruntime
  size_mb: 1175
  resources: { threads_env: NER_THREADS, threads_default: 8, mem_gb: 2.5 }
  thresholds: SPEC 第 3 节（1 万字 ≤ 15 秒，追问 500 字 ≤ 3 秒）
  approved: V2Q-37（2026-10-25）
  smoke: python -m app.cli model-check ner
```

RapidOCR 也登记一项：`source` 写 pip 包 `rapidocr-onnxruntime==1.4.4`，`dir` 为空、`smoke` 用现有的断网 OCR 冒烟；它的模型由 pip 锁版本保证，不另建 MANIFEST。

## 二、获取与入库

1. 只在开发机上、在新建的空目录中下载，按 `source` 固定到提交或版本号；下载前经用户确认（V31Q-06）。
2. 需要格式转换（导出 ONNX、量化）的，在仓库外的一次性容器中做，转换脚本放 `backend/scripts/`（或对应容器目录的 `scripts/`）并在 `convert` 中写明；产出文件算 SHA-256 写入 `MANIFEST.sha256`。
3. 许可证原文放 `deploy/licenses/models/<id>/`，随发布包分发。
4. 模型文件不进 Git；`.gitignore` 按目录忽略、只放行 `MANIFEST.sha256`。
5. 不在运行时下载，不做在线检查与自动升级（V31Q-25）；只在每个版本的规划任务（-00）中检查一次上游有无新版，有则走第七节。

## 三、打包与交付

1. **一个参数**：`build_release.sh <版本> --models <目录>`。目录下按 `id` 分子目录（`<目录>/ner/`、`<目录>/embedding/`、`<目录>/asr/` …）。脚本逐项读 `models.yaml`：以仓库中的 MANIFEST 校验来源目录，复制到对应镜像的构建上下文，复制后再校验；缺任一启用的模型即失败。旧参数 `--ner-models`、`--embedding-models` 删除。
2. **镜像内再校验**：每个 Dockerfile 在 COPY 模型后执行 `sha256sum -c MANIFEST.sha256`（现有做法）。
3. **断网冒烟**：构建脚本对每一项在 `--network none` 下运行 `smoke` 命令，全部通过才打包。
4. **发布包**：包含所有镜像、`models.yaml`、`deploy/licenses/models/`；SHA256SUMS 覆盖它们。服务器上不另外放模型文件。

## 四、放置与运行

| 类型 | 判定 | 放在哪里 | 运行方式 |
|-|-|-|-|
| 轻量 | 单次推理秒级以内，加载后内存 ≤ 2.5 GB，与解析、脱敏同步调用 | backend 镜像（worker 加载，app 只校验） | onnxruntime，CPU；常驻 |
| 重型 | 单个任务分钟级以上（转写、翻译），或内存 > 2.5 GB | 独立容器，一组模型一个容器 | 用时加载、用完卸载（模型支持时）；同一时间只处理一个任务，其余排队并显示位次 |

独立容器统一要求（参照 converter）：

* 不接外网：只挂 `/spool` 内存盘（与 converter 同法），或只在 `internal: true` 的内部网络上供 worker 调用；不挂主密钥、原件卷、数据库。
* `read_only`、`cap_drop: ALL`、`no-new-privileges`，`mem_limit` 与 `cpus` 来自 `.env`（第五节）。
* 启动时校验 MANIFEST，不通过则容器不健康；**重型容器不可用只影响它自己的功能**（界面显示“暂不可用”），不影响审核主线。轻量模型校验失败照旧阻止 worker 启动（它们是脱敏主线的一部分）。
* 运行时只用清单中写明的推理库（onnxruntime；本地翻译确认后为 llama.cpp）；任何镜像不装 torch、transformers。

## 五、资源与优先级

1. **全部从 `.env` 调**，代码只读环境变量、给出默认值：`NER_THREADS`、`EMBED_THREADS`（worker 内）；重型容器各一组 `<ID>_CPUS`、`<ID>_MEM`、`<ID>_THREADS`。默认值按基准机（28 vCPU、38 GiB，balloon 下限 16 GiB）设定。
2. **重型功能可整组不启用**：用 compose profiles，`.env` 中 `COMPOSE_PROFILES=asr,translate` 列出启用的组；不列即不启动该容器，对应功能在界面显示“未启用”。硬件不够的服务器只跑主线。
3. **优先级**：交互任务（解析、脱敏、追问、助手对话）优先于后台长任务（转写、翻译）。重型容器 `cpu_shares` 低于其他服务；按段处理，每段开始前若有解析 / 脱敏 / 追问任务在运行则等待（在段落边界让路）；开始前可用内存不足其 `MEM` 时等待（补充项 62）。
4. **内存预算**：所有启用项的 `mem_gb` 加上主线高峰，不超过来宾内存减 4 GiB；balloon 下限低于此值时，在运维说明中提示调整或少启用一组（V31Q-27）。
5. **运维说明给一张推荐值表**，至少三档：基准机；16 vCPU / 24 GiB（只启用一个重型组）；8 vCPU / 16 GiB（只跑主线、线程减半）。每档写明各变量取值与可启用的组。表中数值以 V31-M-01 与各模型选型实测为准。

## 六、换模型与派生数据

1. 换模型、换版本、换量化等同于新增，走第七节。
2. 由模型算出、落库或缓存的数据，必须能随模型变化失效：NER 缓存已把 MANIFEST 计入缓存键（ner.py `cache_context`），保持；向量表（法条、范本条款、知识库、历史合同）换向量模型时，该版本的升级步骤须全部重算，不混用两个模型的向量。
3. 系统状态页“本地模型”一栏：逐项显示 `id`、用途、版本（`source` 的提交或版本号）、校验结果、启用 / 未启用 / 不可用。不显示路径与哈希全文。

## 七、接入新模型的固定步骤

1. **候选**：用途、候选 2～3 个、许可证（商用、地域、署名义务）核实。
2. **下载前确认**：列出文件、来源、大小，经用户同意（V31Q-06）。
3. **选型报告**（docs/v31/reports/ 或当期目录，写短）：效果（虚构样本）、目标机单独运行的速度与内存、**与主线同时运行时主线的变化**（以 SPEC 第 3 节现有门槛判定）、镜像增量、建议的放置类型与 `.env` 默认值。目标机实测须经用户同意（V3Q-24）。
4. **用户确认选型**。
5. **改规则**（Opus）：CLAUDE.md、AGENTS.md 的本地模型条款，SPEC 第 3 节表格。
6. **登记**：`models.yaml` 一项、MANIFEST、许可证原文、转换脚本、`.env.example` 变量、运维说明推荐值表。
7. **测试**：只加该模型的断网冒烟与一个代表性用例；整仓全量在批次末跑一次。

## 八、在另一台服务器上实施（写进运维说明“本地模型”一章）

1. 对照推荐值表，按服务器 vCPU 与内存选档，填 `.env` 的 `COMPOSE_PROFILES` 与资源变量。
2. 按现有步骤 `docker load`、安装。
3. 核对：`docker compose run --rm worker python -m app.cli model-check`（逐项列出校验结果）；重型容器看 `docker compose ps` 健康状态；或在系统状态页“本地模型”一栏查看。
4. 用虚构样本各跑一次：上传一份合同看脱敏；启用的重型组各跑一个短样本。
5. 资源不合适时只改 `.env` 后 `docker compose up -d`，不改代码、不重建镜像。

## 九、V31-M 中的落实

新增任务 **V31-M-02 本地模型统一接入**（GPT-6.1 Sol，medium，依赖 V31-M-01），在接入转写容器之前完成，转写一组按本规范接入：

1. `deploy/models.yaml`，登记现有 ner、embedding、ocr 三项；许可证原文放 `deploy/licenses/models/`。
2. build_release.sh 改为 `--models`，逐项校验、复制、断网冒烟；发布包带清单与许可证。
3. `NER_THREADS`、`EMBED_THREADS` 从环境变量读取（默认 8，app 进程仍为 4）；`.env.example` 与 compose 同步。
4. `app.cli model-check`；系统状态页接口加“本地模型”一栏（界面在 V31-U4）。
5. compose profiles 骨架与重型容器的统一写法（安全选项、资源变量、健康检查），供转写容器直接套用。
6. 运维说明加“本地模型”一章（第八节内容与推荐值表），补 `--models` 用法，更正开头硬件描述。

V31-M-00 细化时把本规范作为 design/V31-M.md 第〇节的引用，转写容器的设计按第四、五节写；推荐值表的数值在 V31-M-01 实测后填。本地翻译（V31Q-48）确认实施时按同一规范接入，不另立做法。

## 十、规格变更（V31-M 审核时由 Opus 合并）

* SPEC 第 3 节：本地模型行改为“清单见 deploy/models.yaml”，保留各模型的资源门槛；“不得引入”一段改为“本地模型以 deploy/models.yaml 为准，新增或更换按本规范第七节”，并列出允许的推理库。
* SPEC 第 20 节（部署）：compose profiles、资源变量、`--models` 参数、发布包附带模型许可证。
* SPEC 管理后台“系统状态”：加“本地模型”一栏。
* CLAUDE.md、AGENTS.md：本地模型条款改为引用 `deploy/models.yaml` 与本规范；常用命令中的发布包构建改为 `--models`。
* docs/ops/运维说明.md：新增“本地模型”一章。
