# V2-B-06a 开发机评测工具

仅用于复核选型报告，不是产品模块。模型、ONNX、大型日志与原始预测放仓库外；audit.json 为本轮固定证据快照。不得把容器退出 0 解释为模型通过业务门槛。

## 本轮重放

PowerShell 在仓库根目录执行。`$evidence` 指向报告所列独立证据目录。已有模型和镜像无需再次下载；下列运行均断网。没有在线自动下载脚本。重建镜像涉及下载依赖，需遵守原批准范围；更换模型须另说明文件 / 来源 / 大小。

```powershell
$evidence = 'C:/Users/Administrator/.codex/visualizations/2026/10/24/01a16bae-05f0-7751-94b2-17bdb1711c79/v2-b/ner-selection'
$scripts = "${PWD}/docs/v2/reports/ner-selection"
docker run --rm --network none -e PYTHONPATH=/srv/backend -v "${PWD}:/srv:ro" -v "${evidence}:/evidence" -w /srv/docs/v2/reports/ner-selection aireview-ner-eval:b06a python prepare_corpus.py --root /srv --out /evidence/corpus-replay.json
docker run --rm --network none -v "${scripts}:/scripts:ro" -w /scripts aireview-ner-eval:b06a python -B verify_metrics.py
docker run --rm --network none --cpus 2 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 -e TOKENIZERS_PARALLELISM=false -v "${scripts}:/scripts:ro" -v "${evidence}:/evidence" aireview-ner-eval:b06a python /scripts/run_bert_onnx.py /evidence/exported/bert --corpus /evidence/corpus.json --out /evidence/bert-int8-replay.json
docker run --rm --network none --cpus 2 -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 -e TOKENIZERS_PARALLELISM=false -v "${scripts}:/scripts:ro" -v "${evidence}:/evidence" aireview-ner-eval:b06a python /scripts/run_gliner_onnx.py /evidence/exported/gliner --filename model.onnx --splitter characters --corpus /evidence/corpus.json --out /evidence/gliner-fp32-replay.json
docker run --rm --network none -v "${scripts}:/scripts:ro" -v "${evidence}:/evidence" -w /scripts aireview-ner-eval:b06a python -B summarize.py /evidence
```

每条命令后应立即把 `$LASTEXITCODE` 单独落盘并回读；有一个失败则不继续把后续结果称为通过。`summarize.py` 读取原始五份预测与实建镜像增量，不读 replay 文件，避免覆盖原始测量。重放另存文件，不改 audit.json。

## 工具与构建

- prepare_corpus.py：现有 compute 基线 + frozen samples → 等长空白遮盖、残余分母、保留项。
- export_bert.py / export_gliner.py：只从本地固定权重导出，分别包含 FP32 与动态 INT8；GLiNER strict 加载补证顺序见报告。
- run_bert_onnx.py / run_gliner_onnx.py：391 节点预测与 10,000 字基准，记录三次耗时 / 加载 / 峰值 RSS。
- compare_gliner.py：原始 PyTorch、FP32 ONNX、INT8 三句数值诊断，不加入计分样本。
- benchmark_scaling.py：原 FP32 模型的多核诊断，分别在 `--cpus 2/8/14` 容器内以对应 `--threads 2/8/14` 运行；显式同步 Torch / ORT 线程。结果不代替原门槛或目标机器容量验收。
- score.py / verify_metrics.py / summarize.py：固定网格校准与独立计分；无镜像实建证据时门禁 null。
- verify_evidence.py：核对下载与导出文件 SHA-256、冻结样本 / corpus、原始预测与计分快照、断网 / CPU 配额及独立退出码；在上述 /scripts、/evidence 挂载下执行 `python -B verify_evidence.py /evidence`。
- Dockerfile.eval：隔离导出 / 评测依赖。最后一层是首次失败后的 onnx / ml_dtypes 修复，保留演进方便核对日志。
- Dockerfile.bert-runtime / Dockerfile.gliner-runtime：实际 backend:dev 上构建候选资源镜像，仅复制 INT8 运行产物。构建上下文分别为 evidence/exported/bert、gliner；需将对应 Dockerfile 复制到上下文，同时用 .dockerignore 排除原 safetensors、FP32 ONNX、vocab.txt 与 spm.model 等未 COPY 文件。不能将评测镜像大小冒充产品增加量。

本轮 eval image `sha256:66e5c7c996d4d75b4907b148a3c30f07263182d9567b09cb40eafc6a7db38268`；运行基线 `sha256:828dbeb6eb2397e73268065b189ff0fbd1f76aa1465666023139b9f1d06ccc15`。BERT INT8 候选 `sha256:38470e236efd0732127adefb03d1c3e1a1b42811a48566990ca86fbc50def981`，GLiNER INT8 候选 `sha256:50de6d33d6cb55f2de8449c45c7c9e3456d5d9367cc541054c3aa896be750fa6`。模型及这些候选镜像均未进入产品部署。
