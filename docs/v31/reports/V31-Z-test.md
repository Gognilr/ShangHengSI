# V31-Z-01 整仓回归与本机补查

2026-11-09 · 设计见 [V31-Z.md](../design/V31-Z.md) 第一节。工作区含此前未提交的改动（57 个文件），回归在该状态下进行。

## 一、自动检查

| 项 | 结果 |
|-|-|
| 整仓全量（CLAUDE.md 命令） | **3747 passed、3 skipped、0 failed**，36 分 55 秒。3 个跳过为 `NER_MODEL_EVAL=1` 才运行的模型评测 |
| 前端构建（vue-tsc + vite） | 通过（仅有 chunk 超 500 kB 提示） |
| converter 自身测试 | 22 个通过 |
| 实体识别模型“偶发加载失败”（V31-S-07） | 整仓跑中未再现 |

## 二、本机浏览器补查（表中编号对应设计第一节）

环境：本机开发栈（asr、模拟模型），Vite 5173，本机开发账号（该账号是管理员）。

| # | 结果 |
|-|-|
| 1 | **部分完成**：1366×640，智能助手首页、智能审核首页、会议纪要、翻译、审核历史用 DOM 检查，均无横向溢出（scrollWidth = 1366，无元素越过右边缘）；设置个人页截图目视正常。其余分辨率 / 特效组合与确认页、审核中、结果页未做。浏览器面板的深色模拟不改变应用自身主题，夜幕与精简特效需在应用“外观”里切换，本次未切 |
| 2、3、4 | **未做**：精简特效弹层、`--disable-gpu` 的 Edge、Edge 性能面板帧率都要在员工电脑 / 真实 Edge 上操作，转 Z-03 员工电脑检查 |
| 5、6 | **未做**：T5 录音全流程、T6 三份 Word 导出，本会话没有走完，转 Z-03 准备数据时一并做 |
| 7、8 | **未做**：需要双模型模拟服务和一个普通用户账号；本机仅有管理员测试账号，建普通账号未试 |
| 9 | **已修**：原来转写等不到内存只显示“转写超时”。现在 transcriber 区分等内存超时（写 `TIMEOUT_MEMORY`），后端映射 `memory_timeout`，界面提示“服务器空闲内存不足，稍后点重试转写”。已过相关后端测试（7 个）与前端构建；未在真实低内存场景中触发 |

## 三、修复清单

文件：`transcriber/main.py`、`backend/app/parsing/transcribe.py`、`frontend/src/components/assistant/MediaRow.vue`、`frontend/src/api/assistant.ts`。未提交。transcriber 镜像需重建后生效。

## 四、清理

**未执行**：删除残留容器与网络的操作被权限检查拒绝，需用户决定。候选清单（均为本项目历次测试残留，`aireview-test-db`、开发整套 `aireview_default` 保留）：

* 已退出的容器：`aireview-eval-e07-runner`、`aireview-e07-db`
* 网络：`aireview-e08retry-20261027`、`aireview-m508qa`、`aireview-v2f_default`、`aireview-v31s-check_default`、`aireview-z03_default`
* 数据卷：`aireview-e07-db-data`、`aireview-eval-data`、`aireview-eval-r02-data`（可能与 Z-02 评测有关，不建议删）
