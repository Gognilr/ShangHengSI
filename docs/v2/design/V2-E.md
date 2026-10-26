# V2-E 法规库与向量检索

2026-10-24 · Opus 5.5 方案稿 · 用户已确认（2026-10-24，V2Q-25～V2Q-29）
2026-10-26 · Opus 5.5 细化（V2-E-00）· 用户已确认（2026-10-26，含 V2Q-76～V2Q-84；CLAUDE.md 外网条已按 12.1 修改）

范围见 [PLAN.md](../PLAN.md) 第三节 E1～E7；执行规则见 [TASKS-V2.md](../TASKS-V2.md) 第一节。本文件是 V2-E 各任务之间的接口契约，任务卡自包含。规格变更汇总在 [SPEC-V2.md](../SPEC-V2.md) E 部分。来源：2026-10-24 调研 GitHub 同类项目（LawRefBook/Laws、OpenContracts、Anthropic legal 插件等）后的讨论；2026-10-26 在官网取得真实样本后细化（第一节）。

## 〇、结论与共同约定

| 项 | 内容 | 任务 |
|-|-|-|
| E1 | 法规库：存国家法律法规数据库的法规目录（标题、分类、公布与施行日期、时效性）与常用法规全文；同名法规的各版本分别存储 | V2-E-01、V2-E-04 |
| E2 | 手动导入：业务管理员上传官方导出的目录表格（上传时选定时效性，V2Q-76）与批量下载的全文压缩包，先看差异再确认生效；只增改、不删除 | V2-E-01、V2-E-04 |
| E3 | L-10 改为读法规库：从 12 部固定清单扩展到法规库中全部已废止的法律、行政法规、司法解释 | V2-E-02 |
| E4 | 可选自动更新：worker 按月或按周从官方数据库拉取目录变化与新增法规全文，生成待确认差异；默认关闭 | V2-E-03 |
| E5 | 向量检索基础：PostgreSQL 的 pgvector 扩展（db 改为自建镜像）+ 一个 CPU 本地向量模型（V2-E-04a 选型） | V2-E-04a、V2-E-04 |
| E6 | 法条依据：审核与追问时从本地法规库检索相关法条原文，随脱敏正文发给模型；模型只能引用给出的编号，本地核实引用 | V2-E-05 |
| E7 | 公司范本库：业务管理员上传公司标准合同，审核时逐条对照，标出偏离；范本有而本文件缺的条款本地报 L-12 | V2-E-06 |
| E8 | 智能助手知识库：复用 E5，约束见第十一节 | V2-D（V2-D-00 细化） |

**顺序**：V2-A → V2-B → V2-C → **V2-E** → V2-D（V2Q-25）。管理入口依赖 V2-C 的角色拆分（C6）与数据保留（C2）；E6、E7 改变审核结果，放在 A、B 之后单独评测；V2-D 的知识库直接复用 E5。

**不做**：Celery、Redis 等外部队列；独立的向量数据库服务；部门规章（官方数据库不收录）；向量检索走云端接口（等于把原文发出去）；绕过官网验证码。

### 共同约定

1. **开工条件**：V2-C 已通过批次审核（V2-C-09）；本文件与 V2Q-76～V2Q-84 经用户确认。执行顺序：E-01 → E-02 → E-04a（Opus，可与 E-01、E-02 并行）→ E-04 → E-03 → E-05 → E-06 → E-07 → E-08 → E-09；E-03 用到 E-04 的全文暂存表与拆条，排在 E-04 之后。V2-E-04 另需 V2-E-04a 的选型报告经用户确认、CLAUDE.md 与 SPEC-V2 第 3 节列入所选向量模型。
2. **测试**：每节测试用例逐条写成测试，不得删改，可以多写；单个任务只跑列出的测试文件。V2-E-02、03、05、06 测试先行。凡用到 pgvector 的测试（V2-E-04 起）改用自建的 db 镜像作测试库（第五节第 2 条），CLAUDE.md 常用命令同步（第十二节）。
3. **样本**：官方样本只在开发机临时目录，不进仓库。测试用 `backend/tests/fixtures/laws/make_law_fixtures.py` 生成的虚构小样本（虚构法规标题与条文，如《示例供货合同法》）；L-10 与迁移测试可以使用现行 abolished\_laws.yaml 中的真实法规标题（公开信息）。BIFF 格式的 .xls 样本无法用 openpyxl 生成，生成脚本另写一个最小 BIFF8 文件，或在测试中直接模拟 converter 返回 xlsx（与 test\_legacy.py 相同做法）。
4. **迁移编号**：V2-C 用到 0016。本批 V2-E-01 用 0017、V2-E-02 用 0018、V2-E-04 用 0019、V2-E-05 用 0020、V2-E-06 用 0021；V2-E-03 不需要迁移。实际编号与此不同时按“最大编号加一”顺延并在批次问题文件中记录（同 V2Q-53）。
5. **外网**：只有 `backend/app/llm/gateway.py` 与 `backend/app/lawsync/client.py` 两个文件可以导入网络库（T-20 扩展，第四节）；其余新代码一律不得访问网络。
6. **权限**：法规库、公司范本、“审核时附带相关法条”归业务管理员（`require_biz_admin`）；“法规库自动更新”归系统管理员（`require_sys_admin`）；兼任的 admin 两者都可。任务相关的检索与向量全部按 owner\_id 过滤，非本人 404（A-06）。
7. **日志**：不得记录法规导入文件内容、范本名称与条款、检索查询文本、检索到的范本内容、向量；可以记录数量、耗时、导入记录 id。法规标题虽是公开信息，日志中也只写数量，避免与任务关联。
8. **外发**：任务外发策略为“不外发”（local\_only）时不检索、不外发，行为与 V1.0 相同。
9. **提交**：只暂存本任务文件；提交说明以任务编号开头，用英语或日语。
10. **向量的安全定位**（方案稿 2.3，用户已确认）：向量由原文计算，存在被部分反推出原文的可能，因此与对应原文同等对待——任务条款向量随“原件与原文”层删除，范本条款向量随范本删除，法条向量是公开数据不受限制；向量无法加密后再建索引，只存放在本地数据库中，所有检索都在服务端按 owner\_id 或资源范围过滤，任何接口不返回向量；查询文本的向量只在内存中计算、不落库（任务条款向量除外，见 5.6）；日志不记录查询文本和检索到的范本内容。

## 一、官方数据实测（V2-E-00，2026-10-26，用户同意下载样本）

在开发机下载了官网“批量导出文件目录”的一份目录（40 部，9 KB）和与“批量下载文件”同源的 5 个全文文件（共约 430 KB），并读了页面接口。结论如下，已体现在后面各节：

| 项 | 实测结果 | 对设计的影响 |
|-|-|-|
| 目录文件格式 | 扩展名 .xlsx，实际是旧版 Excel 二进制格式（OLE / BIFF8，文件头 `D0CF11E0`），openpyxl 读不了 | 按文件内容而不是扩展名识别；BIFF 交给现有 converter 转成 xlsx 后再读；因此导入改为 worker 任务（app 不能访问 converter），V2Q-77 |
| 目录列 | 只有 4 列：标题、公布日期、施行日期、法律法规分类；**没有时效性、制定机关、唯一编号** | 时效性由管理员上传时选定（按官网“时效性”筛选后导出，一份文件一种时效性），V2Q-76；键改为“标题 + 公布日期”，V2Q-78 |
| 分类取值 | 与页面二级分类同名：法律、行政法规、司法解释、地方法规、地方性法规、修改、废止的决定、有关法律问题和重大问题的决定（部分）等 | category 存原文；L-10 只认“法律、行政法规、司法解释”三类，V2Q-84 |
| 导出范围 | 只导出当前页勾选的条目（每页最多 100 条） | 一次上传允许多份文件（同一时效性） |
| 批量下载 | 浏览器端把多个文件打成 zip，文件名为 `{标题}_{公布日期 YYYYMMDD}.docx`；**部分文件实际是 .doc**（司法解释样本即是） | 全文按文件名中的标题与日期对应目录；.doc 交 converter，V2Q-77 |
| 全文结构 | 标题、题注（“（1999年3月15日……通过）”）、目录、编 / 章 / 节标题、条文“第X条”+ 全角空格；条文的后续款项是独立段落；司法解释用“一、……”分部分；页脚是页码 | 现有 `parse_docx` 可直接读取；按第五节规则拆条，样本条数全部对上（民法典 1260、合同法 428、公司法 2023 版 266、2018 版 218、买卖合同解释 33） |
| 检索接口 | `POST /law-search/search/list` 返回 bbbs（32 位十六进制编号）、标题、公布日期、施行日期、sxx（1 已废止、2 已修改、3 有效、4 尚未生效，个别“决定”为空）、分类、制定机关；`POST /law-search/download/batch` 返回文件地址，文件放在 **flkoss.obs-bj2.cucloud.cn**（对象存储，带签名的临时地址）；站点另有验证码接口 | 自动更新可用；白名单需要两个域名，V2Q-79 |
| 同名多版本 | 官方把被替换的旧版本标为“已修改”（公司法 2018、2013 版），法律被废止时**全部版本**都标“已废止”（海洋环境保护法 2017、2023 版） | 维持已确认的“全部版本都已废止才报 L-10”，与官方数据一致 |
| 现有 12 部清单 | 9 部在官方库中可查（取得公布日期写入迁移）；经济合同法、涉外经济合同法、技术合同法官方库未收录 | 这 3 部 published\_on 为空，仍作为内置数据（第三节） |

法规正文不受著作权法保护（著作权法第五条），可以存储和发送。LawRefBook/Laws 只作参考，代码不得复制。

## 二、法规库表与目录导入（V2-E-01）

**文件范围**：`backend/app/laws/__init__.py`、`backend/app/laws/catalog.py`（新：表格读取与差异）、`backend/app/laws/imports.py`（新：导入记录、确认、放弃）、`backend/app/laws/job.py`（新：law\_import 处理函数）、`backend/app/admin/laws.py`（新：接口）、`backend/app/main.py`（只注册路由）、`backend/app/worker.py`（只把 law\_import 加入解析组、导入处理函数模块）、`backend/app/admin/alerts.py`（只加 LAW\_IMPORT\_PENDING）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0017_laws.py`（新）、`backend/tests/fixtures/laws/`（新）、`backend/tests/test_laws_catalog.py`（新）、`backend/tests/test_admin_laws.py`（新）、`backend/tests/test_migrations.py`、`backend/tests/test_worker.py`（只追加）。前端在 V2-E-07。

### 2.1 数据表（迁移 0017，down\_revision=0016）

**laws**（一部法规的一个版本一行）

| 列 | 类型 | 说明 |
|-|-|-|
| id | bigint 主键 | |
| title | varchar(300) 非空 | 官方标题原文（去首尾空白） |
| category | varchar(60) 非空 | 官方“法律法规分类”原文 |
| office | varchar(100) | 制定机关；目录表格没有此列，自动更新时填写 |
| status | varchar(10) 非空 | not\_yet / valid / amended / repealed（尚未生效、有效、已修改、已废止） |
| published\_on | date | 公布日期；只有内置数据可以为空（第三节） |
| effective\_on | date | 施行日期，可空 |
| repealed\_on | date | 废止日期；官方目录不提供，只有内置数据有值 |
| replaced\_by | varchar(300) | 现行规定的标题，业务管理员手工填写，可空 |
| flk\_id | varchar(32) 唯一，可空 | 官方编号 bbbs，自动更新时填写 |
| source | varchar(10) 非空 | builtin / import / sync，最后一次写入的来源 |
| fulltext\_at | timestamptz | 全文写入时间（V2-E-04 使用），可空 |
| created\_at、updated\_at | timestamptz 非空 | |

约束：`UNIQUE (title, published_on) NULLS NOT DISTINCT`（PG 16 支持）；status、source 的 CHECK；索引 `(title)`。

**law\_imports**（每次导入或自动同步一行）

| 列 | 类型 | 说明 |
|-|-|-|
| id | uuid 主键 | |
| kind | varchar(10) 非空 | catalog / fulltext / sync |
| status | varchar(10) 非空 | parsing / pending / applied / discarded / failed |
| declared\_status | varchar(10) | catalog：上传时选定的时效性；其他为空 |
| file\_names | jsonb 非空 | 上传的文件名列表（sync 为空列表） |
| added、changed、unchanged、skipped | integer 非空，默认 0 | 统计 |
| diff | jsonb | 差异明细（2.3 第 5 条） |
| error | varchar(200) | 失败原因（固定文字，不含文件内容） |
| created\_by | uuid | sync 为空 |
| created\_at | timestamptz 非空 | |
| applied\_by、applied\_at | | 确认或放弃的人与时间 |

**law\_import\_files**（上传文件的临时存放，解析结束即删除）：id、import\_id（外键，级联删除）、seq、name varchar(255)、data\_enc bytea。文件内容用主密钥加密（与原件同等对待：管理员可能误传内部文件），以 `_enc` 结尾使主密钥更换命令自动包含。

### 2.2 上传（`POST /admin/law-imports`，multipart，业务管理员）

* 字段：`kind=catalog`、`status`（not\_yet / valid / amended / repealed，必填）、`files`（1～10 个，每个 ≤ 5 MB，扩展名 .xls 或 .xlsx）。fulltext 在 V2-E-04 中启用，本任务收到 kind=fulltext 返回 422。
* 已有 parsing 或 pending 的导入记录时 409 `LAW_IMPORT_PENDING`“有一项法规库更新尚未处理，请先确认或放弃”（同一时间只有一个未完成的导入，避免差异过期）。
* 写入 law\_imports（parsing）与 law\_import\_files，入队 `law_import`（payload 只含 import\_id），返回 202 `{id}`。审计 `law_import_upload`（kind、文件数、status）。

### 2.3 解析任务（`law_import`，worker 解析组）

1. **识别格式**（按内容，不看扩展名）：zip 头 → 按 17.6 的 OOXML 检查（`filecheck.check_ooxml`）后用 openpyxl 只读打开；OLE 头 → `filecheck.check_ole(ext="xls")`，经 `parsing.legacy` 交 converter 转为 xlsx 后同上；其他 → 失败“不是官方导出的目录表格”。加密、含宏、转换失败沿用现有提示。
2. **表头**：每份文件第一个工作表前 5 行中第一行非空行为表头，列名去空白后精确匹配：`标题`、`公布日期`、`法律法规分类` 必需；`施行日期`、`制定机关`、`时效性` 可选。缺少必需列时整次导入失败：“第 {n} 个文件缺少列：{列名}”。有“时效性”列且取值为“尚未生效 / 有效 / 已修改 / 已废止”之一时，该行以此列为准，否则用上传时选定的时效性（官网以后若增加此列可直接使用）。
3. **行**：每份文件最多 5,000 行、合计 20,000 行，超过时整次失败。标题去首尾空白、≤ 300 字；日期接受 `YYYY-MM-DD` 文本与 Excel 日期值。标题为空、公布日期为空或不合法、标题超长的行跳过，记入 skipped（文件序号、行号、原因，明细最多 200 条）。同一次导入中“标题 + 公布日期”重复的行只取第一行，重复数记入 diff。
4. **比对**：按“标题 + 公布日期”找 laws：不存在 → 新增；存在且 status、category、effective\_on（文件中有值时）任一不同 → 变化，逐项记录“旧值 → 新值”；否则未变。**只增改、不删除**：文件里没有的法规保持原样。replaced\_by、repealed\_on、flk\_id 不受影响。
5. **diff 结构**：`{"added": [{title, category, status, published_on, effective_on}], "changed": [{law_id, title, published_on, changes: {字段: [旧, 新]}}], "skipped": [{file, row, reason}], "duplicates": n}`。写入后 status=pending，删除 law\_import\_files。任何一步失败：status=failed、error 写固定原因、删除 law\_import\_files，不写 laws。
6. 日志只写 import\_id、各项数量与耗时。

### 2.4 确认与放弃

* `POST /admin/law-imports/{id}/apply`：锁住该记录，必须为 pending，否则 409。对 changed 中每一条核对当前值仍等于“旧值”、added 中每一条仍不存在，任一不符时 409 `LAW_IMPORT_STALE`“法规库在此期间已变化，请放弃后重新导入”且不写任何数据；全部相符时在同一事务中写入（source=import，updated\_at=现在），status=applied、记录确认人。审计 `law_import_apply`（kind、added、changed，不含标题）。
* `POST /admin/law-imports/{id}/discard`：parsing 以外的未完成记录可放弃，status=discarded。审计 `law_import_discard`。
* 管理员提醒（业务管理员与 admin）：存在 pending 记录时显示 `LAW_IMPORT_PENDING`“法规库有一项更新待确认，请在设置 → 法规库中处理”。

### 2.5 查询接口（业务管理员）

* `GET /admin/laws?q=&category=&status=&page=&page_size=`：分页列表（每页最多 100），返回 id、title、category、status、published\_on、effective\_on、replaced\_by、source、has\_fulltext；另返回各时效性的数量与总数。
* `PATCH /admin/laws/{id}`：只允许修改 replaced\_by（≤ 300 字，可清空）。审计 `law_update`。
* `GET /admin/law-imports`：最近 20 条记录（不含 diff）；`GET /admin/law-imports/{id}`：含 diff。

### 2.6 测试用例（T-41）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 上传虚构的 OOXML 目录（4 列，含新增 2 部、时效性变化 1 部、未变 1 部），选定“已废止” | 202；任务完成后 pending，added=2（status=repealed）、changed=1（列出旧 → 新）、unchanged=1；laws 尚未改变 |
| 2 | 同 1 后确认 | laws 写入；source=import；审计 law\_import\_apply 只含数量 |
| 3 | 放弃 | laws 不变，status=discarded |
| 4 | 文件头为 OLE 的 .xlsx（模拟 converter 返回 xlsx） | 经 converter 转换后解析成功；converter 不可用时失败并给出原因 |
| 5 | 缺少“公布日期”列 | failed，error 为“第 1 个文件缺少列：公布日期” |
| 6 | 有“时效性”列 | 该列取值优先于上传时选定的时效性 |
| 7 | 文件中没有的已有法规 | 确认后仍在、不变 |
| 8 | 两份文件中同一“标题 + 公布日期”重复 | 只取第一行，duplicates=1 |
| 9 | 标题为空、日期不合法的行 | skipped 记文件序号、行号、原因，其他行正常 |
| 10 | 已有 pending 时再上传 | 409 LAW\_IMPORT\_PENDING |
| 11 | pending 期间另一途径改了同一部法规（测试中直接改库） | apply 返回 409 LAW\_IMPORT\_STALE，不写数据 |
| 12 | 普通用户、系统管理员调用导入接口 | 403 |
| 13 | 解析结束（成功或失败） | law\_import\_files 无该导入的行 |
| 14 | 日志 | 不含标题与文件内容（caplog） |
| 15 | 迁移 0017 升级 / 降级 | 三张表创建与删除；模型与迁移一致 |
| 16 | `PATCH /admin/laws/{id}` 修改 replaced\_by；尝试修改 status | 前者成功并审计；后者字段被忽略或 422，status 不变 |
| 17 | 提醒 | 有 pending 时业务管理员看到 LAW\_IMPORT\_PENDING，普通用户看不到 |

## 三、L-10 改读法规库（V2-E-02，测试先行）

**文件范围**：`backend/app/checks/references.py`、`backend/app/checks/service.py`（只把法规索引传给 check\_references）、`backend/app/laws/index.py`（新）、`backend/app/db/alembic/versions/0018_laws_builtin.py`（新）、`backend/resources/abolished_laws.yaml`（删除）、`backend/tests/test_checks_references.py`、`backend/tests/test_checks_service.py`、`backend/tests/test_migrations.py`。

1. **索引**：`laws.index.load(db, titles: set[str]) -> LawIndex`：只查文件中书名号内出现的标题。别名派生：标题以“中华人民共和国”开头的，去掉前缀后的写法也算该法（其他别名不派生）；查询时对文件中的每个写法同时查原样与加“中华人民共和国”前缀两种。只取 category 为“法律”“行政法规”“司法解释”的行，以及 source=builtin 的行（V2Q-84）。
2. **判定**：一个标题的全部版本都是 repealed 时才报 L-10；有一个版本是 valid、amended 或 not\_yet 就不报。法规库中没有的标题不报（与 V2-A 相同，不推断）。
3. **文字**：`引用《{原写法}》，该法已于 {废止日期} 废止`；废止日期取各版本 repealed\_on 的最大值，都为空时写“该法已废止”；replaced\_by 有值（取任一版本的非空值，优先公布日期最新的版本）时加“，现行规定见《{replaced\_by}》”。严重程度、位置合并方式与 V2-A 相同（同一文件同一法规一条，多个位置合并）。
4. **迁移 0018**：把 abolished\_laws.yaml 的 12 部写入 laws（category=法律、status=repealed、source=builtin、repealed\_on、replaced\_by）。公布日期取官方库（第一节）：合同法 1999-03-15、担保法 1995-06-30、物权法 2007-03-16、民法通则 2009-08-27、民法总则 2017-03-15、侵权责任法 2009-12-26、中外合资经营企业法 2016-09-03、中外合作经营企业法 2017-11-04、外资企业法 2016-09-03；经济合同法、涉外经济合同法、技术合同法官方库未收录，published\_on 为空。用 `ON CONFLICT DO NOTHING`（重跑与恢复不覆盖）。降级删除 source=builtin 且未被导入改写过的行（updated\_at = created\_at）。之后不再读取 yaml 文件，文件删除；vat\_rates.yaml 不变。
5. 新安装、未导入任何目录时，L-10 的效果与 V2-A 完全相同（12 部、相同文字）。
6. **故障**：读取法规库失败时 L-10 跳过，记一条警告日志（不含标题），其他校验照常。

测试用例（T-42）：

| # | 场景 | 期望 |
|-|-|-|
| 1 | 只有内置数据，文件引用《合同法》与《中华人民共和国合同法》 | 报 L-10，文字与 V2-A 逐字相同（含 2021-01-01 与民法典） |
| 2 | 导入《示例供货合同法》两个版本：旧版 amended、新版 valid | 不报 |
| 3 | 两个版本都 repealed，均无 repealed\_on、无 replaced\_by | 报“该法已废止”，无“现行规定见” |
| 4 | 一个版本 repealed、一个 not\_yet | 不报 |
| 5 | 法规库中没有的标题 | 不报 |
| 6 | 已废止的地方性法规同名标题 | 不报（只认三类） |
| 7 | 别名：库中标题“中华人民共和国示例法”，文件写《示例法》 | 识别为同一法 |
| 8 | 库中标题不以“中华人民共和国”开头，文件写去掉部分字的简称 | 不识别 |
| 9 | replaced\_by 有值 | 文字带“，现行规定见《…》” |
| 10 | 读取法规库时抛异常 | L-10 无结果，其他 L 项正常，日志一条警告且不含标题 |
| 11 | 迁移 0018 升级 | 12 行 builtin；与已有同键行冲突时不覆盖；降级只删未被改写的 builtin 行 |
| 12 | 同一文件多处引用同一废止法 | 一条 L-10，位置合并（V2-A 原有用例保留） |

## 四、法规自动更新（V2-E-03，测试先行）

**文件范围**：`backend/app/lawsync/__init__.py`、`backend/app/lawsync/client.py`（新，唯一可访问网络的法规模块）、`backend/app/lawsync/sync.py`（新：比对与写入待确认记录）、`backend/app/lawsync/job.py`（新：定时检查与处理函数）、`backend/app/admin/data.py`（只加自动更新设置与“立即同步”接口）、`backend/app/admin/settings_store.py`（只加默认值）、`backend/app/admin/alerts.py`（只加 LAWSYNC\_FAILED）、`backend/app/worker.py`（只把 law\_sync 加入维护组、导入模块）、`backend/app/config.py`（只加 LAWSYNC\_PROXY）、`deploy/docker-compose.yml`（只给 worker 加 LAWSYNC\_PROXY）、`deploy/.env.example`、`backend/tests/test_no_network_imports.py`、`backend/tests/test_lawsync.py`（新）、`backend/tests/test_admin_data.py`（只追加）。

1. **client.py**（只有它可以 `import httpx`，网关除外）：
   * 常量 `API_HOST = "flk.npc.gov.cn"`、`FILE_HOST = "flkoss.obs-bj2.cucloud.cn"`（V2Q-79），只用 HTTPS，`follow_redirects=False`，超时 30 秒，读取环境变量 `LAWSYNC_PROXY`（空则直连）。
   * 只有三个函数，参数只接受下列类型，运行时校验，不符抛 ValueError：`list_page(category_codes: tuple[int, ...], status_codes: tuple[int, ...], page: int, page_size: int) -> dict`（POST `/law-search/search/list`，请求体只含这四类固定条件）；`download_urls(flk_ids: list[str]) -> list[str]`（POST `/law-search/download/batch`，每个 id 必须匹配 `^[0-9a-f]{32}$`，最多 100 个）；`download_file(url: str) -> bytes`（GET，URL 的主机必须等于 FILE\_HOST、路径以 `/prod/` 开头，响应 ≤ 20 MB）。
   * 每两次请求间隔不少于 2 秒（模块内计时）；每个响应 ≤ 5 MB（文件 ≤ 20 MB），超过即中止。
   * 响应按不可信输入处理：JSON 必须 `code == 200` 且字段类型符合预期（rows 中 bbbs 为 32 位十六进制、title 为 ≤ 300 字的字符串、日期为 `YYYY-MM-DD` 或 null、sxx 为 1～4 或 null），否则抛 `SyncError("BAD_RESPONSE")`；返回 HTML、验证码或非 200 状态时抛 `SyncError("BLOCKED")`；网络错误抛 `SyncError("NETWORK")`。不重试、不处理验证码。
2. **同步流程**（`law_sync`，维护组）：
   1. 定时检查（`register_periodic`，每小时）：`lawsync.enabled` 为 true、距 `lawsync.last_run_at` 已满 30 天（monthly）或 7 天（weekly）、没有 parsing / pending 的导入记录时入队一次。`POST /admin/lawsync/run`（系统管理员）立即入队一次（开关关闭时 409；有未完成导入时 409 LAW\_IMPORT\_PENDING）。
   2. 拉目录：分类 法律（codeId 102，含其下级）、行政法规（210）、司法解释（320、330、340），时效性 1～4，每页 100，逐页直到取完；总页数超过 60 页即中止（异常数据保护）。sxx 为空的行跳过。
   3. 比对规则同 2.3 第 4 条，键为“标题 + 公布日期”；新行与已有行写入 flk\_id；内置行同键时只更新 status、category、office、effective\_on。
   4. 拉全文：本次新增且 status 为 valid 的法律、司法解释，按公布日期从新到旧最多 200 部：`download_urls` 分批取地址、`download_file` 逐个下载，docx / doc 按第五节规则拆条（doc 经 converter），结果写入暂存表 law\_import\_articles（5.1 第 3 条）；单个文件失败只记入 diff 的 fulltext\_failed，不中止。
   5. 结果写成一条 kind=sync、status=pending 的 law\_imports（diff 另含 `fulltext: [{title, published_on, articles}]`），提醒业务管理员确认（LAW\_IMPORT\_PENDING），**不自动生效**。确认流程同 2.4，全文部分按 5.3 写入。
   6. 任一步抛 SyncError：本次不写 laws、不留下 pending 记录（已写的暂存行删除），`lawsync.last_status = {"ok": false, "at": 时间, "reason": 代码}`；成功时 `{"ok": true, ...}`，并更新 `lawsync.last_run_at`。
   7. 审计 `law_sync_run`（请求数、结果代码、added、changed、全文数），不写 llm\_calls。
3. **设置**（系统管理员，`GET / PUT /admin/settings/lawsync`）：`enabled`（默认 false）、`interval`（monthly / weekly，默认 monthly）；GET 另返回 last\_run\_at、last\_status。修改写审计 `lawsync_settings`。
4. **提醒**：`lawsync.last_status.ok == false` 且开关开启时，系统管理员看到 `LAWSYNC_FAILED`“法规库自动更新失败，可改用手动导入（设置 → 法规库）”；下一次成功或关闭开关后消失。
5. **T-20 扩展**：test\_no\_network\_imports.py 的允许名单改为 `{llm/gateway.py, lawsync/client.py}`；另断言 client.py 中出现的域名字符串只有上述两个、`follow_redirects` 不为 True、三个公开函数的参数名与注解与第 1 条一致、项目中没有其他模块导入 client 以外的网络库。

测试用例（T-43）：

| # | 场景 | 期望 |
|-|-|-|
| 1 | AST 扫描 | 只有 gateway.py 与 lawsync/client.py 导入网络库；其他文件导入 httpx 时测试失败 |
| 2 | client.py 的域名常量与函数签名 | 与第 1 条一致；`download_file("https://example.com/prod/a.docx")` 抛 ValueError，不发请求 |
| 3 | `download_urls(["../etc"])`、传入字符串类型的分类 | ValueError，不发请求 |
| 4 | MockTransport 返回 302 到其他域名 | 不跟随，SyncError |
| 5 | 返回 HTML 或验证码页面 | SyncError("BLOCKED")，不写 laws，last\_status.ok=false，系统管理员提醒出现 |
| 6 | 返回的 rows 中 bbbs 不合法或字段类型不符 | SyncError("BAD\_RESPONSE")，整次放弃 |
| 7 | 正常返回 2 页（含 1 部新法、1 部时效性变化、1 部 sxx 为空） | 生成 1 条 sync pending：added=1、changed=1，sxx 为空的跳过；laws 未变；确认后写入并带 flk\_id |
| 8 | 新增的有效法律有全文（docx 与 doc 各一，doc 模拟 converter） | 暂存表有两部的条文；确认后写入 law\_articles 并入队 law\_embed |
| 9 | 单个全文下载失败 | 记入 fulltext\_failed，其余照常 |
| 10 | 两次请求的间隔 | 不少于 2 秒（测试中替换计时函数断言调用间隔） |
| 11 | 开关关闭 | 定时检查不入队；`POST /admin/lawsync/run` 409 |
| 12 | 已有 pending 导入 | 不入队；立即同步 409 LAW\_IMPORT\_PENDING |
| 13 | 业务管理员调用自动更新设置接口 | 403 |
| 14 | 同步成功 | 审计 law\_sync\_run；llm\_calls 无新增 |
| 15 | 函数入参 | 测试断言三个函数都不接受任务、文本或任意字典参数（签名检查） |

## 五、pgvector、向量引擎与全文导入（V2-E-04）

**依赖**：V2-E-04a 选型报告经用户确认（模型名、维度 D、文件与 SHA-256、阈值），V2-E-02 完成。

**文件范围**：`deploy/db/Dockerfile`（新）、`deploy/docker-compose.yml`（db 改用自建镜像）、`deploy/scripts/build_release.sh`、`deploy/scripts/lib.sh`、`deploy/scripts/upgrade.sh`、`deploy/scripts/restore.sh`（只改镜像名与检查）、`docs/ops/运维说明.md`（只改镜像清单与测试库命令）、`backend/app/embedding/__init__.py`、`backend/app/embedding/engine.py`（新）、`backend/models/embedding/MANIFEST.sha256`（新，入仓）、`.gitignore`（忽略模型文件）、`backend/Dockerfile`（模型文件与校验）、`backend/requirements.txt`（只加 `pgvector`，锁定版本）、`backend/app/main.py` 与 `backend/app/worker.py`（只加启动自检与 law\_embed 注册）、`backend/app/review/clauses.py`（新：一级条款划分）、`backend/app/laws/articles.py`（新：拆条）、`backend/app/laws/fulltext.py`（新：全文导入）、`backend/app/laws/job.py`、`backend/app/laws/imports.py`、`backend/app/admin/laws.py`（启用 kind=fulltext）、`backend/app/tasks/pipeline.py`（只在解析结束时计算条款向量）、`backend/app/backup/retention.py`（原件层同时删除条款向量）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0019_pgvector.py`（新）、对应测试（test\_embedding.py、test\_law\_articles.py、test\_laws\_fulltext.py、test\_clause\_vectors.py 为新；test\_migrations.py、test\_retention.py、test\_pipeline.py、test\_scripts.py 只追加）。

### 5.1 pgvector

1. **db 镜像**：`deploy/db/Dockerfile` 为 `FROM postgres:16.15`，用 PGDG 软件源安装 `postgresql-16-pgvector`（锁定 V2-E-04 时的精确版本号，写入 Dockerfile 与运维说明），不改数据目录与入口脚本。镜像名 `aireview-db:{版本}`；compose 的 db 改用它（带 build 段，与 app 相同的 build-begin / build-end 标记）；build\_release.sh、lib.sh 的镜像清单用它替换 postgres:16.15；upgrade.sh 加载新镜像后照常 `up -d`（同为 PG 16，数据目录直接沿用，不需要导出导入）。
2. **测试库**：从 V2-E-04 起测试数据库改用 `aireview-db:dev`（`docker build -t aireview-db:dev deploy/db`），CLAUDE.md 常用命令同步（第十二节）。
3. **迁移 0019**：`CREATE EXTENSION IF NOT EXISTS vector`（compose 中数据库账号 aireview 由官方镜像以 POSTGRES\_USER 创建，是超级用户，有权限；已核对 deploy/docker-compose.yml）。新建：
   * `law_articles`：id bigint 主键、law\_id（外键级联删除）、article\_no varchar(30)、seq integer、text text 非空、embedding vector(D) 可空；HNSW 索引 `vector_cosine_ops`；`(law_id, seq)` 唯一。
   * `law_import_articles`（暂存）：id、import\_id（外键级联删除）、title varchar(300)、published\_on date、article\_no、seq、text。
   * `task_clause_vectors`：id、task\_id（外键级联删除）、file\_seq integer、first\_loc varchar(20)、locs jsonb（该条款的全部节点位置）、embedding vector(D) 非空、model varchar(60)；索引 task\_id。
   * 降级：删除三张表，`DROP EXTENSION IF EXISTS vector`。
4. **备份与恢复**：pg\_dump 照常（会包含 `CREATE EXTENSION vector`）；恢复必须在带 pgvector 的 db 镜像上进行，restore.sh 在恢复前检查 `SELECT 1 FROM pg_available_extensions WHERE name='vector'`，没有时拒绝并提示“当前数据库镜像不含 pgvector，请先按升级步骤更换 db 镜像”。V1.0 的旧备份可以恢复到 V2：恢复后迁移会补建扩展与新表。

### 5.2 向量引擎（`app.embedding.engine`）

* 接口：`embed(texts: list[str], kind: Literal["query", "passage"]) -> np.ndarray`，返回 L2 归一化的 float32 矩阵（n × D）；按模型要求加前缀（如 e5 的 `query: ` / `passage: `），由选型报告给出；每段最多 512 个 token，超出截断；每批 ≤ 16 段。`dim() -> int`、`model_id() -> str`。
* 只用 onnxruntime + numpy + tokenizers，不装 torch、transformers、sentencepiece；关闭 ORT 遥测；worker `intra_op_num_threads=8`，app 每进程 4。
* `check(load: bool = False)`：模型文件缺失、SHA-256 与 MANIFEST 不符或加载失败时拒绝启动（同 ner.check）。app 启动只校验文件，第一次追问检索时才加载（V2Q-81）；worker 启动时加载。
* 日志不记录输入文本与向量。

### 5.3 全文导入（`kind=fulltext`）

1. 上传：一个 zip（≤ MAX\_UPLOAD\_MB），沿用 17.6 的解压限制（条目 ≤ 200、单个文件解压后 ≤ 20 MB、合计 ≤ 200 MB、拒绝绝对路径与 `..`）。存入 law\_import\_files，入队 law\_import。
2. 解析：逐个条目按文件名 `{标题}_{YYYYMMDD}.(docx|doc)` 取标题与公布日期（从右边第一个下划线切开）；对不上格式、扩展名不是 docx / doc、目录、在 laws 中找不到同键法规的，记入 unmatched（文件名与原因，最多 200 条），不写入。docx 用 `parse_docx`；doc 经 converter 转 docx（按内容识别）。拆条后写入 law\_import\_articles。
3. diff：`{"matched": [{law_id, title, published_on, articles, replace: bool}], "unmatched": [...]}`；added 记新增全文数、changed 记替换已有全文数。
4. 确认：在同一事务中把每部法规的旧条文删除、写入新条文（embedding 为空），fulltext\_at=现在，删除暂存行；然后入队 `law_embed`。放弃：删除暂存行。
5. 建议的全文范围（写入设置页说明文字）：有效的法律与司法解释全部导入，行政法规按需导入；地方性法规只导目录。

### 5.4 拆条规则（`laws.articles.split(nodes) -> list[Article]`）

1. 只看正文节点（part=body），按顺序处理；页眉页脚不参与。
2. **条文开头**：去掉段首空白后匹配 `^第([零〇一二三四五六七八九十百千]+)条(之[一二三四五六七八九十]+)?[　 \t]+`；article\_no 为匹配到的“第…条（之…）”，正文为其后的文字。
3. **标题行**（不并入条文）：`^第[零〇一二三四五六七八九十百千]+(编|分编|章|节)[　 ]`；去掉空白后等于“总则”“分则”“附则”；`^[一二三四五六七八九十]+、` 且不超过 30 字、不含“。”。
4. 第一条之前的段落（标题、题注、目录、序言）丢弃；之后不是条文开头也不是标题行的段落、表格单元格文字，作为新的一行并入当前条文。
5. 一个条文都没有识别到时，该文件记入 unmatched“未识别到条文”。

### 5.5 向量化任务（`law_embed`，维护组）

* 每批取 64 条 embedding 为空的 law\_articles（`"《标题》第…条：正文"` 作为 passage），计算后写回，每批一个事务，可中断续做；全部完成后结束。启动时与每次全文确认后入队（已有排队中的不重复入队）。
* 6 万条法条的耗时与内存在 V2-E-08 测试虚拟机上实测。

### 5.6 任务条款向量（V2Q-81）

1. **一级条款**（`review.clauses.first_level(nodes)`）：按与 `review.service.chunk_boundaries` 相同的判据切分正文——每个文件的第一个正文节点、标题节点、clause\_no 不含“.”的节点各自开始一个新条款，直到下一个开始点；条款标题取首个节点文字前 60 字。分段审核、法条检索、范本对照、L-12 共用这一划分。
2. 解析成功结束时（pipeline 的最后一步），对每个一级条款计算 passage 向量（条款文字超过 2,000 字时截取前 2,000 字），写入 task\_clause\_vectors（同一任务先删后写）。计算失败不让解析失败：记警告日志（只含 task\_id），该任务没有条款向量（之后的检索在 worker 中临时计算，第二步不显示范本建议）。
3. 条款向量与原文同等对待（共同约定第 10 条）：保留期“原件与原文”层删除原件时一并删除；任务删除时级联删除；不经任何接口返回；只在服务端按 task 的 owner\_id 校验后使用。
4. 升级前已解析的任务没有条款向量：检索时临时计算，不补算入库。

### 5.7 测试用例（T-44）

| # | 场景 | 期望 |
|-|-|-|
| 1 | 迁移 0019 | 扩展与三张表创建；降级删除；模型与迁移一致（测试库为 aireview-db 镜像） |
| 2 | 用第一节的结构造虚构全文 docx（题注、目录、编章节、“第一千零一条”、“第一百二十条之一”、多段条文、司法解释“一、”分部） | 条数、article\_no、各条正文与期望逐条相同；目录与题注不入条文 |
| 3 | 段落以“第三十条规定的”开头（无全角空格） | 不当作新条文，并入上一条 |
| 4 | zip 中有 `示例法_20200101.docx`（库中有）、`示例法_20200102.docx`（库中无）、`说明.txt`、`../x.docx` | 第一个匹配；第二个、第三个 unmatched；第四个整包拒绝 |
| 5 | zip 中的 .doc（模拟 converter） | 转换后拆条成功 |
| 6 | 确认全文导入 | law\_articles 替换为新条文，embedding 为空，fulltext\_at 有值，law\_embed 入队；放弃时 law\_articles 不变 |
| 7 | law\_embed（引擎用测试替身） | 全部条文有向量；中途抛异常后重跑可续做 |
| 8 | 引擎：模型文件改动 | app、worker 启动失败并说明原因（不含文件内容） |
| 9 | 引擎：同一文本两次 embed | 结果一致，范数为 1，维度为 D（真实模型的对拍放在 V2-E-08，参考向量由 V2-E-04a 产出） |
| 10 | 解析完成 | task\_clause\_vectors 行数等于一级条款数；向量计算抛异常时解析仍成功、无向量 |
| 11 | 原件层保留期清理 | 该任务的 task\_clause\_vectors 被删除；任务删除时级联删除 |
| 12 | 任何接口的返回 | 不含 embedding 字段（遍历 OpenAPI schema 断言） |
| 13 | restore.sh：目标库没有 vector 扩展（test\_scripts 中模拟） | 拒绝恢复并提示更换 db 镜像 |
| 14 | build\_release.sh、lib.sh 镜像清单 | 含 aireview-db:{版本}，不含 postgres:16.15 |
| 15 | 日志 | 不含条款文字与法条正文 |

## 六、向量模型选型实测（V2-E-04a，Opus）

方法同 V2-B-06a：**下载候选模型与评测用的法规全文之前向用户说明文件名、来源、大小并取得同意**，只在开发机下载，在断网容器中评测；产出 `docs/v2/reports/V2-E-04a.md`（选型报告）、SPEC-V2 第 3 节与 CLAUDE.md 修改稿、`backend/models/embedding/MANIFEST.sha256` 草稿、参考向量（对拍用）。用户确认后 V2-E-04 才开工。

1. **候选**（许可均需在选型时核实可商用）：BAAI/bge-small-zh-v1.5、intfloat/multilingual-e5-small、BAAI/bge-m3（FP32 约 2.2 GB，超过镜像门槛时只评估 INT8 版本，并说明量化来源）。外贸合同有英文，倾向多语言模型。
2. **评测集**（Opus 自建，虚构合同条款 + 真实公开法条，进 `backend/tests/fixtures/embedding/`）：
   * 法条检索：100 个“合同条款 → 应引用的法条”（民法典合同编、买卖合同解释等）；
   * 范本对照：中文范本 30 条 + 对应的改写条款、英文条款各 30 条，另有 20 条无对应条款；
   * 范本选择：5 份虚构范本、10 份虚构合同。
3. **通过线**：法条检索前 10 名召回 ≥ 85%；中英文条款对照范本时最佳匹配正确率 ≥ 90%；1 万字向量化 ≤ 5 秒（worker 8 线程）；worker 单进程内存增加 ≤ 1 GB；app 首次加载后单进程增加 ≤ 1 GB；镜像增加 ≤ 1.5 GB。
4. **阈值**（V2Q-83，在同一评测集上确定并写入选型报告，V2-E-05、06 直接使用）：法条检索最低相似度 `LAW_MIN_SCORE`；范本选择平均相似度 `TEMPLATE_SELECT_MIN`；条款对照 `CLAUSE_PAIR_MIN`（同时决定 L-12 的“未找到”）。取值以“无对应条款的 20 条误配为 0、对应条款召回最高”为准。
5. 多个候选都通过时选内存和镜像增量最小的。

## 七、法条依据（V2-E-05，测试先行）

**文件范围**：`backend/app/laws/retrieve.py`（新）、`backend/app/review/job.py`、`backend/app/review/prompt.py`、`backend/app/review/schema.py`、`backend/app/review/validate.py`、`backend/app/review/coverage.py`、`backend/app/review/service.py`（只改结果页输出模型 FindingOut 与 \_View）、`backend/app/chat/service.py`、`backend/app/export/report_docx.py`、`backend/app/admin/laws.py`（只加“附带相关法条”开关）、`backend/app/admin/settings_store.py`（只加默认值）、`backend/resources/prompts/system.md`（升级提示词版本）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0020_law_refs.py`（新）、对应测试（test\_law\_refs.py 为新；test\_review\_prompt.py、test\_review\_validate.py、test\_review\_job.py、test\_chat.py、test\_report\_docx.py、test\_coverage.py、test\_migrations.py 只追加）。

1. **开关**：`laws.refs_enabled`（默认 false），`GET / PUT /admin/settings/laws`（业务管理员），修改写审计 `laws_settings`。关闭时外发内容与 V2-C 完全相同（除提示词版本与新增的 law\_refs 字段说明外）。
2. **检索**（在 worker 的审核任务中进行，V2Q-82）：
   * 查询：本文件（分段审核时为本段）每个一级条款的条款向量（task\_clause\_vectors；没有时用 engine 临时计算，不入库），加上每条适用审核要求文字的 query 向量（worker 进程内按“内置要求版本 + 公司补充要求版本”缓存）。
   * 候选：`laws.status = 'valid'`、category 为“法律”或“司法解释”、embedding 非空的 law\_articles；每个查询取余弦相似度前 5 名且 ≥ LAW\_MIN\_SCORE。
   * 合并：同一法条取最高分，按分数从高到低，累计不超过 20 条、8,000 字（某条放不下就跳过它，继续看下一条）。
3. **逐条预检**：每条拼成 `《标题》第…条：正文` 后单独执行出口复检（`recheck`，与整段复检同一组规则、同一组已记住名称与自定义敏感词）；有任何命中的整条丢弃，计入 dropped。
4. **外发**：用户消息在【待审核文件】之后增加 `【参考法条】`，每条一行：`[LAW3]《中华人民共和国民法典》第五百八十五条：……`（编号从 LAW1 起，按本消息内顺序；用 LAW 前缀而不是方案稿中的 F，避免与位置编号 F1-P23 混淆，V2Q-82）；文字经 `escape_document_tags`。分段审核时每段各自检索、各自编号。之后整段外发文本照常在网关出口复检；确认发送时（app 的 create\_run）的预检只检查正文部分，不变（app 不做检索）。
5. **提示词**（system.md 升级版本）：增加一条“【参考法条】只用于支持你的判断。引用法律时只能使用【参考法条】中的编号，写入 law\_refs；不得凭记忆写法律名称和条号；没有【参考法条】或没有合适的法条时 law\_refs 为空，basis 写通行商业惯例等依据”。JSON 结构的 finding 增加 `"law_refs": ["参考法条编号，如 LAW3"]`；`fixed_prompt_text` 同步包含【参考法条】等固定用词。
6. **本地核实**：
   * law\_refs 中不是本消息编号的丢弃；有效编号转成快照 `{title, article_no, text}`（公开文本，明文）存入 findings.law\_refs，之后法规库更新也不影响旧结果。
   * problem、basis、suggestion 中出现的 `《…》第…条`：法规库中该标题（含第三节第 1 条的别名）没有任何 valid / amended / not\_yet 版本，或该法有全文而最新有效版本中没有这一条时，findings.law\_unverified=true，结果页显示“引用未经法规库核实”；法规库没有该法全文时只核对时效性。
7. **追问**（app，同步）：开关开启时，用问题原文（本地计算 query 向量，只在内存中）按第 2 条规则检索最多 5 条，逐条预检后以 `【参考法条】` 附在问题之前的上下文中；向量模型在 app 进程第一次需要时加载（同 NER，V2Q-81）。
8. **覆盖说明**：`law_refs: {"enabled": bool, "sent": n, "dropped": m, "chars": c}`；中文描述“本次附带相关法条 n 条（因出口复检丢弃 m 条）”，开关关闭时不显示。
9. **展示与报告**：结果页输出模型（review/service.py 的 FindingOut；review/schema.py 中同名的是模型输出结构，见第 5 条）增加 `law_refs: [{title, article_no, text}]`、`law_unverified: bool`；Word 报告在每个问题之后列出“依据：《标题》第…条”及条文全文。
10. **迁移 0020**：findings 增加 `law_refs jsonb 非空 默认 '[]'`、`law_unverified boolean 非空 默认 false`、`source varchar(10) 非空 默认 'model'`（CHECK model / template，V2-E-06 使用）。

测试用例（T-45）：

| # | 场景 | 期望 |
|-|-|-|
| 1 | 开关关闭 | 外发 user 消息与 V2-C 逐字相同；无【参考法条】 |
| 2 | 开关开启，检索结果 25 条、合计 12,000 字 | 发送 ≤ 20 条、≤ 8,000 字，按分数排序 |
| 3 | 某条法条含已记住的名称（测试中把“示例公司”记为名称并放进虚构条文） | 该条不发送，dropped=1；整段复检通过 |
| 4 | 已废止、已修改法规的条文，地方性法规条文 | 不被检索 |
| 5 | 分段审核（两段） | 每段各自检索，编号各自从 LAW1 开始 |
| 6 | 模型返回 law\_refs ["LAW2", "LAW99"] | 只保留 LAW2，存为快照 |
| 7 | 问题说明写“《中华人民共和国合同法》第一百零七条” | law\_unverified=true |
| 8 | 写“《示例供货合同法》第三条”，库中有效且有全文、有第三条 | law\_unverified=false；没有第三十三条时为 true |
| 9 | 法规库更新（替换全文）后查看旧轮次 | 依据仍显示当时的条文 |
| 10 | 追问（开关开启，引擎替身） | 消息中有【参考法条】且最多 5 条，经逐条预检；关闭时与 V2-C 相同 |
| 11 | 外发策略“不外发” | 不检索、不调用引擎 |
| 12 | Word 报告 | 问题后列出依据条文 |
| 13 | 覆盖说明 | 记录 sent、dropped、chars 与中文描述 |
| 14 | 网关拦截记录 | 法条段不会让整段被拦（逐条预检已丢弃命中的条文）；若仍被拦，行为与 V2-C 相同 |
| 15 | 他人任务 | 检索与结果接口 404（A-06） |
| 16 | 迁移 0020 | 三列创建与删除；旧数据 law\_refs=[]、law\_unverified=false、source=model |
| 17 | 提示词版本 | 已升级；fixed\_prompt\_text 含新固定用词 |

## 八、公司范本库（V2-E-06，测试先行）

**文件范围**：`backend/app/standards/__init__.py`、`backend/app/standards/service.py`（新：上传、拆分、加密、选择）、`backend/app/standards/compare.py`（新：对照与 L-12）、`backend/app/standards/job.py`（新：template\_embed）、`backend/app/admin/standards.py`（新：接口）、`backend/app/main.py`、`backend/app/worker.py`（只注册）、`backend/app/review/service.py`（create\_run 记录所用范本；任务详情返回范本建议）、`backend/app/review/job.py`、`backend/app/review/prompt.py`、`backend/app/review/schema.py`、`backend/app/review/validate.py`、`backend/app/review/coverage.py`、`backend/app/review/router.py`（只加请求体字段）、`backend/app/chat/service.py`（只把 `_redact` 提供给范本脱敏复用，不改追问行为）、`backend/app/checks/service.py`（L-12 的排序与显示）、`backend/app/export/report_docx.py`、`backend/app/tasks/service.py` 与 `backend/app/tasks/schemas.py`（任务详情返回 template\_suggestion）、`backend/resources/prompts/system.md`（升级提示词版本）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0021_templates.py`（新）、对应测试（test\_standards.py、test\_standards\_compare.py 为新；test\_runs\_api.py、test\_review\_job.py、test\_review\_prompt.py、test\_review\_validate.py、test\_rotate\_key.py、test\_migrations.py、test\_report\_docx.py 只追加）。

1. **管理**（业务管理员）：`GET / POST /admin/templates`、`GET / PATCH / DELETE /admin/templates/{id}`、`PATCH /admin/templates/{id}/clauses/{cid}`。
   * 上传：DOCX（17.6 上传检查，≤ MAX\_UPLOAD\_MB），填写名称（≤ 100 字）、文件类型（多选，与公司补充要求同一套取值）、立场（多选）。在 app 中同步解析：`parse_docx` 后按 5.6 第 1 条划分一级条款，每条 ≤ 5,000 字（超出 422），条数 1～200（0 条时 422“未识别到条款”）。范本总数（含停用）≤ 50。
   * 每条可选填三项，各 ≤ 300 字：标准立场、可接受范围、需上报的情形。
   * 可以启用 / 停用、改名称与适用范围；不能替换文件（要换就删除后重新上传）。
   * 审计：template\_create、template\_update、template\_delete、template\_clause\_update（只含 id 与数量）。
2. **存储**：standard\_templates（id uuid、name\_enc、doc\_types jsonb、stances jsonb、enabled、content\_hash char(64)（全部条款原文与三项说明的 SHA-256）、clause\_count、created\_by、created\_at、updated\_at）；standard\_clauses（id、template\_id 外键级联、seq、title\_enc、text\_enc、stance\_enc、acceptable\_enc、escalate\_enc（可空）、embedding vector(D) 可空）。名称与条款都是公司内部内容，加密存储（17.3）；向量明文（共同约定第 10 条）。改三项说明后重算 content\_hash。保存后入队 `template_embed`（维护组）计算条款向量；向量未算完的范本不参与选择。
3. **选择**（app，只用已存向量与 SQL，不加载模型）：任务的主文件类型属于范本的文件类型、立场属于范本的立场、范本启用且向量齐全；对每份候选计算“本文件每个一级条款与该范本条款的最高相似度”的平均值，取最高且 ≥ TEMPLATE\_SELECT\_MIN 的一份。任务没有条款向量或没有候选时不建议。
   * `GET /tasks/{id}` 增加 `template_suggestion: {id, name} | null`（只在 awaiting\_confirm 时计算）。
   * 第二步显示“将对照公司范本：{名称}”，可改为“不对照”；`POST /tasks/{id}/runs` 请求体增加 `use_template: bool`（默认 true）。create\_run 重新计算一次选择，结果写入 review\_runs.template\_id、template\_name\_snapshot\_enc、template\_hash；use\_template=false 或无合适范本时都为空。
4. **对照**（worker）：对本文件每个一级条款找相似度最高且 ≥ CLAUSE\_PAIR\_MIN 的范本条款组成对照对；按相似度从高到低最多 30 对、合计 1 万字，超出的写进覆盖说明（“范本对照共 n 对，因长度只发送前 m 对”）。分段审核时每段只发送本段条款的对照对。
5. **外发**：用户消息在【参考法条】之后（没有时在【待审核文件】之后）增加 `【公司范本对照】`：每对一段，`[T1] 本文件位置：F1-P12（条款标题）` + `范本条款：…` + 三项说明中有值的项。范本文字（条款与三项说明）用与追问相同的脱敏函数（`chat.service._redact`，带本任务对照表、已记住名称、自定义敏感词与我方主体预置 B5），新分配的代号写入本任务对照表（与追问相同的加锁方式）。整节拼好后先单独执行一次出口复检，**有命中时整节省略**，覆盖说明写“公司范本对照未通过出口复检，本次未发送”（V2Q-82）；之后整段照常在网关复检。
6. **提示词**（升级版本）：增加“【公司范本对照】中的范本条款是我方标准；对照后有实质偏离的，按偏离对我方的影响提出问题，并在 source 中写‘范本对照’”。finding 增加可选字段 `"source": "范本对照 或省略"`；validate 映射为 findings.source='template'，其他值或省略为 'model'。
7. **缺失条款 L-12**（worker，审核任务开始时）：选中范本的每个条款，与本文件全部一级条款的最高相似度都低于 CLAUSE\_PAIR\_MIN 时，写一条 local\_findings：check\_code=L-12、severity=low、locs 为空、detail\_enc 为“公司范本中的『{条款标题}』在本文件中未找到对应内容”、ai\_summary 为固定文字“公司范本中有条款在本文件中未找到对应内容”。每次审核先删除本任务旧的 L-12 再写；不对照时只删除。L-12 不进入本次的【本地校验摘要】（摘要在确认发送时已生成），只在结果页本地校验区显示。迁移 0021 把 L-12 加入 check\_code 约束。
8. **记录与展示**：review\_runs 增加 template\_id（外键 ON DELETE SET NULL）、template\_name\_snapshot\_enc、template\_hash；结果页显示“本轮对照公司范本：{名称}”，范本之后被修改（hash 不同）或删除时加“（范本已修改）”“（范本已删除）”；问题卡片 source=template 时显示“范本对照”标签；Word 报告同样标注。
9. **主密钥更换**：新增的 `_enc` 列由 rotate-key 自动包含（测试断言）。
10. 这一项替代 PLAN“不在 V2”中的“我方标准条款库”。

测试用例（T-46）：

| # | 场景 | 期望 |
|-|-|-|
| 1 | 上传 12 条款的虚构范本 | 12 行 standard\_clauses，名称与条款为密文，template\_embed 入队 |
| 2 | 上传无法划分条款的文件、第 51 份范本 | 422 |
| 3 | 改三项说明 | content\_hash 变化，审计只含 id |
| 4 | 选择：两份候选，平均相似度 0.82 与 0.65（引擎替身给定向量），阈值 0.7 | 建议 0.82 的那份；都低于阈值时不建议 |
| 5 | 文件类型或立场不匹配、停用、向量未算完 | 不参与选择 |
| 6 | use\_template=false | review\_runs.template\_id 为空，外发无【公司范本对照】，旧 L-12 被删除 |
| 7 | 对照对 40 对 | 发送 30 对以内且 ≤ 1 万字，覆盖说明写明截断 |
| 8 | 范本条款含本公司全称（已在我方主体预置中） | 外发中为我方代号；新代号写入对照表，结果页还原正确 |
| 9 | 范本条款含已记住的客户名称，脱敏后仍命中自定义敏感词（构造） | 整节省略，覆盖说明写明，审核照常完成 |
| 10 | 范本 3 个条款在本文件中无对应 | 3 条 L-12（低），文字含条款标题；不进入本地校验摘要 |
| 11 | 模型返回 source "范本对照" | findings.source=template；其他值为 model |
| 12 | 修改范本后查看旧轮次 | 显示当时的名称快照与“（范本已修改）” |
| 13 | 删除范本 | 旧轮次 template\_id 置空，显示“（范本已删除）” |
| 14 | 普通用户、系统管理员访问范本接口 | 403；任何接口不返回向量 |
| 15 | rotate-key 的 `_enc` 列清单 | 含 standard\_templates.name\_enc、standard\_clauses 的四个 `_enc` 列、review\_runs.template\_name\_snapshot\_enc、law\_import\_files.data\_enc |
| 16 | 日志 | 不含范本名称、条款文字（caplog） |
| 17 | 迁移 0021 | 表与列创建、删除；L-12 约束；模型与迁移一致 |

## 九、前端（V2-E-07）

**文件范围**：`frontend/src/views/settings/Laws.vue`（新）、`frontend/src/views/settings/Templates.vue`（新）、`frontend/src/views/settings/Data.vue`（只加“法规库自动更新”）、`frontend/src/views/settings/SettingsLayout.vue`、`frontend/src/router/index.ts`、`frontend/src/api/laws.ts`（新）、`frontend/src/api/templates.ts`（新）、`frontend/src/api/admin.ts`、`frontend/src/api/tasks.ts`、`frontend/src/api/runs.ts`、`frontend/src/components/ConfirmStep.vue`、`frontend/src/views/ReviewResult.vue` 及其问题卡片组件、`frontend/src/utils/labels.ts`。颜色只用 theme.css 中已有的变量；需要新变量时只追加到 theme.css。

1. **设置导航**（V2Q-80）：业务管理员可见的左侧导航增加“法规库”“公司范本”两页（`adminScope: 'biz'`），放在“审核要求”之后。
2. **法规库页**：顶部“审核时附带相关法条”开关（说明：开启后每次审核约多 5 千个 token，默认关闭，评测确认效果后再开启）；统计（各时效性数量、有全文数）；列表（搜索、分类与时效性筛选、分页、replaced\_by 编辑）；“导入目录”对话框（时效性必选，说明“请在官网按时效性筛选后导出，一次只导入一种时效性的文件，可多选文件”）；“导入全文”对话框（zip，附 5.3 第 5 条的建议范围）；导入记录列表与差异详情（新增、时效性变化“某法：有效 → 已废止”、跳过行、未匹配文件），确认生效 / 放弃；解析中轮询状态。
3. **数据页**（系统管理员）：“法规库自动更新”面板：开关、频率、上次运行时间与结果、“立即同步”。
4. **公司范本页**：列表（名称、文件类型、立场、条款数、启用开关、向量状态“准备中 / 可用”）；上传对话框；条款列表（标题、原文折叠展开、三项说明编辑）；删除确认。
5. **第二步**：有 template\_suggestion 时显示一行“将对照公司范本：{名称}”与“不对照”开关，提交时传 use\_template。
6. **结果页**：问题卡片显示“依据：《…》第…条”（可展开原文）、“引用未经法规库核实”标签、“范本对照”标签；本地校验区显示 L-12；覆盖说明显示附带法条数与范本对照截断；轮次信息显示“本轮对照公司范本：{名称}”及修改 / 删除提示。
7. 界面文字简体中文；`npm --prefix frontend run build` 通过；浏览器检查在 V2-E-08。

## 十、集中测试与批次审核（V2-E-08、V2-E-09）

**V2-E-08**（GPT-6.1 Sol，产出 `docs/v2/reports/V2-E-test.md`）：

1. 整仓挂载 Docker 全量测试通过（测试库为 aireview-db 镜像）；converter 测试照常。
2. 浏览器清单（四种宽度）：法规库页全部操作（含 OLE 格式目录导入、差异确认与放弃、全文导入）；公司范本页；第二步范本提示与“不对照”；结果页依据、未核实标签、范本对照标签、L-12；数据页自动更新面板；三种管理员角色看到的导航。
3. **评测对比**（模拟模型不够，需真实模型时先征得用户同意）：用 V2-A 评测脚本对比“关 / 开 法条依据”“不对照 / 对照范本”四种组合的问题清单（数量、严重程度分布、引用未核实比例、token 用量），写入报告，作为是否建议开启的依据。
4. **测试虚拟机**（另外准备的测试 VM，不是正式服务器）：导入全部有效法律与司法解释的全文，实测向量化总耗时、worker 与 app 内存、数据库与索引体积；升级演练：v2-C 版本（V1.0 数据目录）→ 本批版本，确认 db 换镜像后数据完整、迁移补建扩展；restore.sh 分别恢复 V1.0 旧备份与本批备份；自动更新在测试 VM 上真实运行一次（需用户同意访问官网），确认结果为待确认差异。
5. 引擎对拍：真实模型对 V2-E-04a 的参考向量逐条余弦 ≥ 0.999。

**V2-E-09**（Opus）：核对用例未被删改；逐行审阅 lawsync/client.py 与 T-20 扩展、外发拼接与逐条预检、范本脱敏与整节复检、向量的删除与权限；审阅 V2-E-08 报告；汇报用户，确认后进入 V2-D-00。

## 十一、智能助手知识库（E8，交 V2-D-00）

只先定约束，细节在 V2-D-00 中设计：

* 业务管理员上传公司制度、流程、产品资料，范围为全公司（“部门与共享”仍不在 V2）；分块后向量化，复用 E5。
* 用户在会话中提问时，检索到的片段与用户的文件一样经过脱敏和出口复检后发送。
* “历史相似条款”只检索用户本人的任务；任务向量随原文层删除（5.6）。

## 十二、要改的规则

### 12.1 CLAUDE.md（修改稿，用户确认后由 Opus 修改）

CLAUDE.md 已在 2026-10-24 按方案稿改好技术栈、外网、外发内容与批次顺序四处。本次细化另需改两处：

| 位置 | 原文 | 新文 | 条件 |
|-|-|-|-|
| 必须遵守 · 外网 ② | 只访问固定的国家法律法规数据库域名 | 只访问固定的国家法律法规数据库域名（flk.npc.gov.cn）及其全文文件存储域名（flkoss.obs-bj2.cucloud.cn） | 已修改（用户 2026-10-26 确认 V2Q-79） |
| 常用命令 · 启动测试用的 PostgreSQL | `postgres:16.15` | `aireview-db:dev`（先 `docker build -t aireview-db:dev deploy/db`；V2-E-04 之前仍可用 postgres:16.15） | V2-E-04 完成时由 Opus 在审核中修改 |

向量模型的名称与文件在 V2-E-04a 选型报告确认后另行写入“必须遵守 · 技术栈”。

### 12.2 SPEC

写在 [SPEC-V2.md](../SPEC-V2.md) E 部分。

### 12.3 PLAN 与 TASKS-V2

PLAN 第三节 V2-E 表已在 2026-10-24 同步，本次不改。TASKS-V2 的 V2-E 表按本文件定稿（E-01 改为只做后端，前端统一在 E-07）。

## 十三、风险

| 风险 | 应对 |
|-|-|
| 官网改版、导出格式变化或增加反爬，导入或自动更新失效 | 按表头名称识别列，缺列时明确报出；自动更新只是可选项，失败即提醒；不绕过验证码 |
| 管理员选错导入文件的时效性 | 差异预览逐条列出“有效 → 已废止”等变化，确认后才生效；同名多版本只在全部废止时才报 L-10 |
| 附带法条后问题清单变长、模型更爱引用法条 | 默认关闭，V2-E-08 评测对比后再建议开启；条数与字数有上限 |
| 范本相似度阈值不准，误报缺失条款 | 阈值用评测集确定；L-12 只报低；第二步可以选择不对照 |
| 向量反推原文 | 与原文同等保留期，本地存放，按 owner 过滤，不经接口返回 |
| db 镜像改为自建，升级与恢复多一个环节 | restore.sh 检查扩展；测试 VM 上演练 upgrade.sh 与 restore.sh |
| app 进程加载向量模型增加内存 | 只在追问检索时懒加载；V2-E-04a 测内存门槛，超出时改为追问不检索并报用户 |

## 十四、新增待确认问题

见 [../questions/V2-E.md](../questions/V2-E.md)：V2Q-76（目录无时效性列）、V2Q-77（BIFF 目录与 .doc 全文走 converter，导入改为 worker 任务）、V2Q-78（以标题 + 公布日期为键）、V2Q-79（自动更新需要两个域名）、V2Q-80（法规库、公司范本做成设置导航的两页）、V2Q-81（向量在哪里计算）、V2Q-82（法条段与范本段的预检位置、LAW 编号）、V2Q-83（相似度阈值在 V2-E-04a 确定）、V2Q-84（L-10 只认三类法规）。
