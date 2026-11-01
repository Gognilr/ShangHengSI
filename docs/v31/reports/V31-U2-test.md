# V31-U2 测试记录

## V31-U2-01（2026-11-02，Opus 5.5）

- 接口类型：`TaskStatus` 加 draft；`TaskFile` 加 parse_state、parse_error；`TaskDetail` 加 busy；`tasksApi` 加 draft 创建、addFiles、removeFile（可 keepalive）、setMain、proceed 与 `TASK_ERROR_MESSAGES`；`Suspect` 加 category、placeholder、kept_by；`RedactionItemPos` 加 detected_by；`decideSuspect` 支持 reset 与 NAME / TERM，`ignoreSuspects` 改为 `batchSuspects(ids, 'ignore' | 'reset')`；运行错误码加 FILES_PARSING、FILE_PARSE_FAILED，删去已不存在的 SUSPECTS_PENDING。`http` 请求选项加 keepalive。
- 文字：草稿状态（info 标签）、NAME / TERM 类别、`KEPT_BY`。`REDACTION_KIND` 的 NAME / TERM 映射为 other（旧组件沿用类别色），画布按 detected_by 另画虚线胶囊；设计 1.3 已同步改写。
- 小工具：`utils/hotkeys.ts`（输入控件与对话框、抽屉内不触发，Esc 例外）、`composables/useTaskPoll.ts`（3 秒、单请求、隐藏暂停）、`composables/usePendingDeletes.ts`（5 秒撤销，新删除替换提示时旧条目立即执行，离开路由 / 卸载 / 关闭标签页时发出）。U2-02～04 接入，本任务不改页面。
- 后端（V31Q-37）：`RedactionItemOut` 加 detected_by；`tests/test_redaction_api.py` 的字段集合断言同步。
- 旧确认页（到 U2-03 整体替换）：去掉“疑似遗漏未处理”的提交闸门与计数徽标。V31-S-02 起每个不确定项默认都是 pending，不去掉则确认页永远无法提交。
- `npm --prefix frontend run build` 通过；`tests/test_redaction_api.py` 57 通过。浏览器检查按约定在 U2-05 集中做。
