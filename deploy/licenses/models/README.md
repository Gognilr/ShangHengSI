# 本地模型许可证

本目录随发布包分发。模型的固定版本、用途和转换方式见 [models.yaml](../../models.yaml)；许可证文本获取日期为 2026-11-03，用户已同意仅下载官方 LICENSE / NOTICE 小文本。请求地址、原始文本 SHA-256、未找到的路径见 [SOURCES.json](SOURCES.json)。没有下载模型权重。

| 模型 | 许可证正文与来源 |
|-|-|
| ner | Apache-2.0，[GLiNER 官方项目 LICENSE](https://raw.githubusercontent.com/urchade/GLiNER/main/LICENSE)；固定模型仓库未提供独立 LICENSE，采用其官方代码项目全文 |
| embedding | MIT，[FlagEmbedding 官方 LICENSE](https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE)，保留 Copyright (c) 2022 staoxiao；固定 bge-m3 模型卡标 MIT，但其仓库没有独立 LICENSE，归档对应官方项目的 MIT 原文，不将它冒充从权重目录取得的文件 |
| ocr | Apache-2.0，[RapidOCR v1.4.4 LICENSE](https://raw.githubusercontent.com/RapidAI/RapidOCR/v1.4.4/LICENSE)，保留 RapidAI 版权行 |
| asr-vad | Apache-2.0，M-01 已保存的 FireRedVAD LICENSE，来源 FireRedTeam/FireRedVAD@c30ec49e8cc69642b0ee65362eba11b9d11c6e54 |
| asr、asr-punc | Apache-2.0，[FireRedASR2S 官方 LICENSE](https://raw.githubusercontent.com/FireRedTeam/FireRedASR2S/main/LICENSE)，覆盖 AED / Punc；Punc 固定权重仓库未提供独立 LICENSE |
| asr-punc 标点底座 | Apache-2.0，[Chinese-BERT-wwm 官方 LICENSE](https://raw.githubusercontent.com/ymcui/Chinese-BERT-wwm/master/LICENSE)，归档为 LERT-LICENSE；hfl/chinese-lert-base 权重仓库未提供独立 LICENSE |
| asr-seg | MIT，M-01 已下载的 sherpa-onnx pyannote segmentation-3.0 包内 LICENSE，保留 Copyright (c) 2022 CNRS |
| asr-spk | Apache-2.0，[3D-Speaker 官方 LICENSE](https://raw.githubusercontent.com/modelscope/3D-Speaker/main/LICENSE) |

模型许可结论沿用已确认的选型记录。上表明确区分权重仓库与对应官方项目；未自行补写版权行。已检查 GLiNER、FlagEmbedding、RapidOCR v1.4.4、FireRedASR2S、3D-Speaker、Chinese-BERT-wwm 与固定 FireRedVAD 的根目录 NOTICE 地址，均返回 404；因此未附加这些项目未提供的 NOTICE。main / master 地址的文本在此次归档中以内容哈希固定，不表示模型版本已更新。

以下为当前归档正文的 SHA-256（原下载文件摘要另见 SOURCES.json）：

| 模型 | LICENSE SHA-256 |
|-|-|
| ner | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| embedding | `587a673933425dbc36ec61268d3b954051b2d3ef3c9b322ede357976055ffdd5` |
| ocr | `3e0af25fdd06aa9586ae97adb00ea927ebe5a3805ac77d2d3a81ce5f55693333` |
| asr-vad | `1eb85fc97224598dad1852b5d6483bbcf0aa8608790dcc657a5a2a761ae9c8c6` |
| asr | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| asr-punc | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
| asr-seg | `14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba` |
| asr-spk | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |
