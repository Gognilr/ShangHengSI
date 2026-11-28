# 交付文档（V3.6.0）

| 文件 | 读者 | 源文件 | 成品（docs/manual/dist/，不进仓库） |
|-|-|-|-|
| 系统架构说明书 | 信息技术、信息安全、管理层 | 系统架构说明书.md、diagrams/ | 系统架构说明书.docx / .pdf |
| 系统使用说明书 | 全体员工（使用篇）、管理员（管理篇） | 系统使用说明书.md | 系统使用说明书.docx / .pdf |
| 运维说明 | 运维 | ../ops/运维说明.md | 运维说明.docx / .pdf |
| 使用速查卡 | 员工，A4 单页 | quickcard/index.html | 商衡SI-使用速查卡.pdf |
| 核心功能演示 | 会议投屏，16:9 | demo/index.html、demo/img/ | 商衡SI-核心功能演示.html（单文件）/ .pdf |

截图来自演示环境（全新数据库、模拟模型、名称带“示例”的虚构合同），不含任何真实数据。发版后界面有变化时重截对应截图，Z 任务顺带更新文中版本号。

## 生成（仓库根目录，PowerShell）

```powershell
# Word：在 backend 测试镜像中用 python-docx 生成
docker run --rm -v "${PWD}/docs:/d" aireview-backend-test python /d/manual/tools/build_docs.py /d /d/manual/dist

# PDF：converter 镜像断网运行，只读挂载本机 Windows 字体（镜像本身不带中文字体）
docker run --rm --network none --entrypoint sh -e HOME=/tmp -v "C:\Windows\Fonts:/usr/share/fonts/win:ro" -v "${PWD}/docs/manual/dist:/w" aireview-converter:3.6.0 -c "fc-cache -f >/dev/null 2>&1; cd /w && soffice --headless --convert-to pdf 系统架构说明书.docx 系统使用说明书.docx 运维说明.docx"

# 演示页：图片内嵌为单个 HTML，可拷到会议室电脑离线打开
python docs/manual/tools/build_demo.py
```

演示页、速查卡与插图的 PNG / PDF 用 Edge 打开对应 HTML 截图或打印（速查卡按 A4、无边距、打印背景）。演示页按 → 翻页，A 自动播放，F 全屏；最后一页的访问地址与联系人由使用方填写。

## 与 SPEC 的出入（留给下一阶段 Z 任务合并）

按 v3.6.0 代码核对时发现，说明书以代码为准：

1. SPEC 第 4 节写“5 个容器”，实际为 db、app、worker、converter、web 加可选的 transcriber（asr）、translator（mt），另有 1 GiB 内存盘 invoice_mem（20.2 已写，第 4 节示意图未画）。
2. SPEC 第 5 节目录树仍是早期结构：frontend/src/views 已有 Assistant、Bid、BidProject、ContractCompare、Invoice、Meeting、Translate 等页面，settings 下有 11 个分页；backend/app 另有 bid、invoice、kb、calc.py、content_drafts.py、local_models.py、models_api.py 等。
3. 运维说明第 1 节未提 invoice_mem，已随本次补上一句。
