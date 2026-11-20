# 商衡SI 标志 · 「衡线」

用户 2026-11-20 在 V35-F-00 选定方向 A「衡线」（三个方向的正稿见 [logo-candidates.html](logo-candidates.html)）。全套总览：[index.html](index.html)。前端替换在 F-01 做。

## 寓意

一页文件，正中一条横线伸出纸边、保持水平：
* 它是**脱敏条**：最敏感的一行遮住了，才可以交出去；
* 它是**秤杆**：放平了称，公允地看双方（衡）；
* 它也是**初审找出的那一行**。

右上角的灵紫星点是大模型：它只在角上，看到的只是遮住之后的文件。

## 构造

* 24 网格；纸框笔画 2，圆角 2，内容框 x 3～21、y 1.5～22.5；横线 y 10～14（高 4），两端伸出纸边 2.5；星点中心 (17.5, 4.5)。
* 纸内细线（衡青浅档）只在大稿（≥64px）出现。
* 16 网格小稿（≤20px）：纸框 1.5，横线高 3，星点改为圆点。

## 文件

| 文件 | 用途 |
|-|-|
| mark-{color,mono,inverse}.svg | 标准稿，20～63px |
| mark-lg-{color,mono,inverse}.svg | 大稿，≥64px（单色版不带细线） |
| mark-sm-{color,mono,inverse}.svg | 小稿，≤20px |
| wordmark-h-*.svg | 横排：图形 + 商衡 + SI，文字已转路径 |
| wordmark-v-*.svg | 竖排：图形在上 |
| favicon.svg | 小稿，`prefers-color-scheme: dark` 下换亮一档 |
| app-icon.svg / app-icon-maskable.svg | 应用图标源文件（渐变底块） |
| app-icon-192.png、app-icon-512.png、app-icon-512-maskable.png、apple-touch-icon-180.png | manifest 与 iOS 图标 |

颜色：color 版衡青 #078A76、灵紫 #6D5BF6、字 #0E1726；inverse 版衡青 #33C0A6、灵紫 #8B7CFF、字 #EDF1F6；mono 版全部 #0E1726。前端里一律改用 theme.css 的变量（SiLogo.vue 内联 SVG），这里的十六进制只属于导出文件。

## 规定

* 图形最小 16px；横排组合最小 96px 宽。
* 四周留白不小于横线高度的 2 倍。
* 不得拉伸、改色、加阴影、旋转（404 彩蛋除外）、放在花底上。
* 渐变只用在应用图标底块。

## 使用位置

| 位置 | 文件 | 尺寸 |
|-|-|-|
| 侧栏顶部 | wordmark-h（随主题），收起时只留图形 | 图形 28px |
| 登录页品牌区 | mark-lg-inverse，带开场动效 | 96px |
| 首页问候 | mark（随主题），带开场动效 | 44px |
| 浏览器标签 | favicon.svg | 16px |
| manifest / iOS | app-icon-192 / 512 / 512-maskable、apple-touch-icon-180 | — |
| Word 报告封面与页眉 | wordmark-h-mono / mark-mono | 页眉 10mm 内 |
| 404 页 | mark-lg，横线旋转 −6°，配文“这一页没找平” | 128px |

## 开场动效（700ms）

1. 纸框淡入 240ms；
2. 横线从中心向两边展开（scaleX 0→1），520ms，`--ease-out`，延迟 60ms；
3. 星点弹亮（scale 0→1，自 −30° 转正），340ms，`--ease-spring`，延迟 360ms。

精简档直接显示终态；关闭档不播放。只动 transform 与 opacity。
