# 不确定项向量参照（V31-S-06）

来源：本仓库 `tests/fixtures/redaction_golden/` 的 52 份虚构样本，仅取 must_replace、must_suspect 与 plain_uncertain 标注。
数据 SHA-256：`f9b03774350a0acc8c1e26aa19606a11bdc857f080b5d49bc5f5a34d3ac76711`。
编码：已获准的离线 bge-m3 INT8，`bge-m3-int8@5617a9f`，仅稠密向量，不加前缀，passage 编码。
表示：将正文节点按顺序用换行连接，取标注文字首处前后各 40 字，中间为 `【候选】`；每份样本同规范文字同标签只取一次。

参照文件 `uncertain_reference.npz` 只含 591×1024 的 float32 单位向量与 591 个布尔普通项标签，不含原文、词条或用户数据。
SHA-256：`c5f75b5efce623700a50044f3a309f56ed941bb144490548d19b3397cc7bf4cc`。
按样本留一交叉验证、k=5，取零敏感项误保留的最小可表示阈值 `0.5973327159881593`；有效候选为 356 条敏感标注与 60 条普通项，误保留 0，普通项可保留 54/60（90%），满足设计的启用条件。细节数字见 `docs/v31/reports/V31-S-vector.json`。

重生成（Docker 内、backend 为工作目录）：

```text
python scripts/eval_uncertain_vectors.py --out /srv/docs/v31/reports/V31-S-vector.json --reference resources/uncertain_reference.npz
```

词表或样本改变后须重新评测，不能沿用旧结论；若仍满足启用条件，须同步 `vector_assist.THRESHOLD` 与本说明中的摘要。生产路径只读参照，不下载、不训练、不保存查询文字或查询向量；本地模型不可用时跳过辅助，继续默认替换。此结果仅代表虚构样本，不是实际文件质量证明。

交叉验证与产品使用相同的逐个查询 float32 矩阵-向量乘法，避免批量乘法的舍入差异越过最小阈值。
