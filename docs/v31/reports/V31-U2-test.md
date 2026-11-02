# V31-U2 测试记录

## V31-U2-01（2026-11-02，Opus 5.5）

- 接口类型：`TaskStatus` 加 draft；`TaskFile` 加 parse_state、parse_error；`TaskDetail` 加 busy；`tasksApi` 加 draft 创建、addFiles、removeFile（可 keepalive）、setMain、proceed 与 `TASK_ERROR_MESSAGES`；`Suspect` 加 category、placeholder、kept_by；`RedactionItemPos` 加 detected_by；`decideSuspect` 支持 reset 与 NAME / TERM，`ignoreSuspects` 改为 `batchSuspects(ids, 'ignore' | 'reset')`；运行错误码加 FILES_PARSING、FILE_PARSE_FAILED，删去已不存在的 SUSPECTS_PENDING。`http` 请求选项加 keepalive。
- 文字：草稿状态（info 标签）、NAME / TERM 类别、`KEPT_BY`。`REDACTION_KIND` 的 NAME / TERM 映射为 other（旧组件沿用类别色），画布按 detected_by 另画虚线胶囊；设计 1.3 已同步改写。
- 小工具：`utils/hotkeys.ts`（输入控件与对话框、抽屉内不触发，Esc 例外）、`composables/useTaskPoll.ts`（3 秒、单请求、隐藏暂停）、`composables/usePendingDeletes.ts`（5 秒撤销，新删除替换提示时旧条目立即执行，离开路由 / 卸载 / 关闭标签页时发出）。U2-02～04 接入，本任务不改页面。
- 后端（V31Q-37）：`RedactionItemOut` 加 detected_by；`tests/test_redaction_api.py` 的字段集合断言同步。
- 旧确认页（到 U2-03 整体替换）：去掉“疑似遗漏未处理”的提交闸门与计数徽标。V31-S-02 起每个不确定项默认都是 pending，不去掉则确认页永远无法提交。
- `npm --prefix frontend run build` 通过；`tests/test_redaction_api.py` 57 通过。浏览器检查按约定在 U2-05 集中做。

## V31-U2-05 集中测试（2026-11-02，Sonnet 5.5）

环境：本机 compose 整套重建（`up -d --build`）+ `npm run dev`，浏览器面板 1366×640 为主，另核对 1440×900、1920×1080；样本为 fixtures 的 contract_basic.docx、quotation_table.docx、quotation.xlsx 与由 redaction_golden 的 persons_narrative / en_spec_suspects 转成的 RTF（含 23 组不确定项）。`npm run build` 通过两次。

**已走通**
- 首页：拖入 1 个、再拖入 2 个、重复拖入同一文件（不重复加入）、删除并撤销、删除到期（刷新后仍已删）、设主文件、改立场、下一步；1366×640 首页空态与就绪态无纵向滚动。
- 确认页：原文 / AI 视野（V）、`]` 切文件页签、J / K 选中不确定项并展开回执栏、B 保留原文（出现撤销提示）、选我方、点胶囊撤销人名（1 秒内完成）、划选补充脱敏（经 mouseup 事件）、确认页再拖入文件（出现“解析中”并在完成后刷新为 3 个文件）。

**发现并已修复**
1. 确认页 1366×640 整页被裁 32px：`.wb` 的 `height:100%` 加 32px 内边距，父级无确定高度，回执栏与画布底部被 `overflow:hidden` 裁掉。改为 `.confirm-step` 弹性列、`.wb` `flex:1` + `border-box`；三个视口复核均无页面滚动。（ReviewNew.vue、ConfirmWorkbench.vue）
2. ConfirmCanvas 控制台 `ReferenceError: Cannot access 'pick' before initialization`：`watch(activeId, …, {immediate:true})` 先于 `pick` 声明执行。把 watch 移到 `clearPick` 之后。（ConfirmCanvas.vue）

**未完成 / 限制（请 U2-06 或后续评审补）**
- 帧率：浏览器面板后台节流 rAF 且报告 prefers-reduced-motion，测不出有意义的数字（空闲 60、切换 1～2 帧均为面板产物）；Edge 性能面板的“完整 ≥55 / 精简 ≥30”未测。
- 未做：浅色外壳与完整 / 精简 / 关闭特效的截图矩阵、首页“解析中”截图、local_only 到结果页、从审核历史打开草稿、助手确认脱敏对话框、重新审核任务不显示文件增删、`T`、真实键盘的 Ctrl+Enter。
- 面板的 `left_click_drag` 不触发画布的划选条（mouseup 路径用事件派发验证可用）；Ctrl+Enter 在面板里除聚焦“确认发送”外还触发了点击，处理器已 `preventDefault`，判断为面板合成按键的副作用，真实键盘未验证。
- 后端：每个 app 进程第一次重算（手动补充脱敏）约 40～60 秒（实体识别模型冷加载），其间画布显示“正在重新计算”；之后很快。既有行为，非 U2 引入，建议 U3 前评估是否在 app 启动时预加载。
- **事故**：本机 compose 的模型配置指向真实 DeepSeek（非模拟服务）。Ctrl+Enter 的合成按键使一个样本任务（fictional 的 RTF 样本，已脱敏）被确认发送并调用了 api.deepseek.com 一次；内容全为虚构，无真实数据。后续测试需改用 local_only 或先把本机模型指向模拟服务。

## V31-U2-06 审核（2026-11-02，Opus 5.5）

- 核对：只上传拖入或选中的文件（`dataTransfer.files`）；原文与代号全部纯文本渲染（无 `v-html`）；离开路由、卸载执行等待中的删除，关闭标签页用 keepalive；快捷键在输入控件与对话框中不触发；助手模式隐藏文件增删、本地校验、范本与模型。
- 修正（V31Q-38）：① `usePendingDeletes.flush()` 改为返回 Promise；首页“下一步”与确认页“确认发送”遇到等待中的删除时先真删并重算，本次不前进、不提交。原写法不等删除完成就 proceed / 创建轮次，已删文件可能参与本地校验或被发给模型。② Ctrl+Enter 聚焦按钮后 600ms 内忽略点击，堵住 U2-05 误发的路径。③ ConfirmCanvas 阴影的 `rgba()` 兜底去掉（令牌已存在，否则 test_frontend_style 失败）。
- `npm --prefix frontend run build` 通过。浏览器面板本次导航被拒，未做界面抽查；帧率与截图矩阵（U2-05 未测项）转 V31-U4 评审一并做。
- 遗留：助手确认脱敏在全屏对话框内，`useHotkeys` 因“有打开的对话框”不触发，助手模式暂无快捷键，V31-U4 助手页面一并处理；实体识别模型冷加载 40～60 秒，V31-P-00 评估启动预加载。
- 规格：SPEC 6.2（一屏首页、拖入即解析、工作台、键盘）、13A 第 1 条（助手模式）、19.4（detected_by）；frontend.md 2.2、2.3、第四节组件表与键盘操作、第五节画布胶囊。
