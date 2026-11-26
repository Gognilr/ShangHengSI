# 物件插画正稿（V36-F-00）

五个模块物件，规格按澄明二版规范第三节第 3 条：线条、模块色、单个不超过 30 个元素、悬停才动，只动 transform 与 opacity。

| 文件 | 模块 | 悬停 |
|-|-|-|
| stack.svg | 文件审核 · 纸叠 | 最上一张抬起，右侧短线伸出（衡线） |
| pair.svg | 合同对比 · 两张纸 | 缎带由左向右展开 |
| table.svg | 标书初审 · 开标桌 | 红边那份抬起 |
| voiceprint.svg | 会议纪要 · 声纹带 | 竖条依次起伏一遍 |
| spread.svg | 翻译 · 对开两页 | 右页的行滑到与左页对齐 |

颜色只用 CSS 变量：`--il`（模块色）、`--il-soft`（模块浅底）、`--il-faint`（模块细线），另用 `--si-paper`、`--si-warn`、`--si-danger`。文件里的十六进制只是变量缺省时的后备值，F-01 迁入 components/brand/illus/ 时改成 Vue 组件并去掉后备值（十六进制只写在 theme.css）。

效果稿 `../illus.js` 由 `node ../build-illus.mjs` 从这五个文件生成，插画以这里为准。
