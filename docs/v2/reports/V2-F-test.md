# V2-F 测试与交接

本批仅本机验证，模型网关使用模拟服务；不操作正式服务器。各任务分别提交，F-05 后交 Opus 执行 F-06。

## F-01 零宽字符

解析节点与任务补充说明、追问 preview/send、助手消息入口删除设计指定的六种字符。新增追问用例修复前失败（已知名称没有沿用原代号）。同名 `cases_zero_width.docx` 含原始零宽字符，`.txt/.yaml` 标注归一后的识别结果；容器脚本验证 DOCX 解析、名称映射、助手输入与六种字符清除通过。既有 golden 加载器直接构造 NodeIn，故原始 DOCX 解析另行验证，不修改公共测试加载器。

Docker / Python 3.12，独立 `aireview-f-test-db`（aireview-db:dev，已核实可用 vector 扩展）：`test_chat.py`、`test_redaction_golden.py`、`test_a03_outbound.py` 共 **268 passed、1 既有警告、exit 0**（409.86 秒）。原始 DOCX 的解析与替换也写入同一新增追问用例，单独复跑。
