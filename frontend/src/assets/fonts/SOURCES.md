# 自带字体来源（V31-U1 设计第二节，V31Q-11）

本目录的 WOFF2 由 `frontend/scripts/build_fonts.py` 生成（2026-11-01），原始 TTF 不进仓库。许可证全文随前端发布在 `/licenses/`。

| 族名 | 来源 | 版本 | 许可证 | 原始文件 SHA-256 |
|-|-|-|-|-|
| SI Sans | google/fonts `ofl/notosanssc/NotoSansSC[wght].ttf`（思源黑体的 Noto 发行版） | 提交 `a85815a42757630ce188fdad368c2dfc444d4773` | SIL OFL 1.1，保留名称 “Source”（族名已改为 SI Sans） | `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da` |
| SI Mono | JetBrains/JetBrainsMono 发布包 `JetBrainsMono-2.304.zip` 中的 `fonts/variable/JetBrainsMono[wght].ttf` | v2.304 | SIL OFL 1.1，无保留名称 | `662a196d58f1183bf2d77428b6d5283fe3f45161ab021bea4036bc98e5cac016` |

| 许可证文件 | SHA-256 |
|-|-|
| `public/licenses/OFL-SI-Sans.txt`（google/fonts 同一提交的 `ofl/notosanssc/OFL.txt`） | `1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9` |
| `public/licenses/OFL-SI-Mono.txt`（发布包中的 `OFL.txt`） | `30f0c136e3c88e422d0791acd97238870f9054a9729bc34cf2ff0d4ed8cac4ad` |

处理：字重轴裁到 400～600；SI Sans 按 `scripts/sans_slices.json`（Google Fonts 对 Noto Sans SC 的字频分片范围，取一次）切片，SI Mono 只保留拉丁字母、数字与常用符号。共 102 片，合计 4.05 MB。
