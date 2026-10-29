# V2-B 脱敏增强

2026-10-23 · Opus 5.5（V2-B-00） · 用户已确认（2026-10-23）

范围见 [PLAN.md](../PLAN.md) 第三节 B1～B8；执行规则见 [TASKS-V2.md](../TASKS-V2.md) 第一节。本文件是 V2-B 各任务之间的接口契约，任务卡自包含。规格变更汇总在 [SPEC-V2.md](../SPEC-V2.md) B 部分。

## 〇、共同约定

1. **开工条件**：V2-A 已通过批次审核（V2-A-09）。本批在 R4 与 V2-A 之后的代码上开发。
2. **测试先行**：V2-B-00 已写入四份回归样本，待修项以 `known_gaps` 登记、当前测试通过：
   * `labels_extended`（V2-B-01）、`foreign_suffixes`、`cn_address_unlabeled`（V2-B-02）、`names_derived_persons`（V2-B-03）。

   规则实现后按对应小节把条目移入 `must_replace`（写明类别）、追加 `must_suspect`，**样本 .txt 与已有标注文字不得改动**。各节测试用例表逐条写成测试，不得删改。
3. **每个改了脱敏规则的任务**都要跑 `tests/test_redaction_golden.py`、`tests/test_a03_outbound.py` 和本节列出的测试文件。
4. **只增不减**：本批所有规则只能让更多内容被替换或列为疑似遗漏；唯一的例外是 B6“撤销误识别”，它只对用户明确操作的那段文字生效。出口复检（`recheck.py`）除第 1.4 节新增类别的对照表比对外不改。
5. **三个入口共用规则**：文件正文（engine）、本次补充说明（NOTE 伪节点）、追问（chat `_redact`）。新规则写在 `detect.py`、`parties.py`、`suspects.py` 或新模块中，engine 与 chat 都调用；不各写一套。
6. **迁移编号**（按 V2Q-31 调整）：V2-A 用了 0006～0008。本批 V2-B-01 用 0009，V2-B-03 用 0010，V2-B-05 用 0011，V2-B-06 用 0012。
7. 日志不得含原文；提交只暂存本任务文件。

## 一、标签扩展与新类别（V2-B-01，测试先行）

**文件范围**：`backend/app/redaction/detect.py`、`backend/app/redaction/placeholders.py`、`backend/app/redaction/engine.py`（新类别接入）、`backend/app/chat/service.py`（新类别接入）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0009_im_project_categories.py`（新，编号按 V2Q-31）、`frontend/src/utils/labels.ts`（只追加类别名称与 REDACTION\_KIND）、对应测试、`labels_extended.yaml`（按 1.5）。

### 1.1 编号类（REFNO，代号“编号\_n”）新增标签

提单号、提单编号、B/L No、Bill of Lading No、信用证号、信用证编号、L/C No、Credit No、报关单号、报关单编号、Customs Declaration No、项目编号、项目号、Project No、招标编号、招标项目编号、Tender No、Bid No、报价单号、报价编号、Quotation No、Quote No、运单号、Waybill No、集装箱号、Container No。值的写法沿用现有 REFNO（字母、数字和 `-/_.`，整串）。标签与值之间的规则沿用现有（冒号、空白、填空括号）。

### 1.2 证件类（IDNO，代号“[证件号]”）新增标签

护照号、护照号码、Passport No、营业执照注册号、注册号、工商注册号、组织机构代码、Organization Code、税号、纳税人识别号、Tax ID、VAT No、TIN。值为 5～30 位字母、数字和 `-`，至少含 2 个数字。

### 1.3 银行代码（归入 SWIFT 类别，代号“[SWIFT\_n]”）新增标签

行号、联行号、开户行行号、大额支付行号、CNAPS、Sort Code、Routing No、ABA、IFSC。值为 6～15 位字母和数字。不新建类别：模型从标签能看出是银行代码；界面名称“SWIFT 代码”改为“SWIFT / 银行代码”。

### 1.4 新类别

| 类别 | 代号 | 标签 | 值 | 还原与复检 |
|-|-|-|-|-|
| IM 即时通信账号 | 共用“[联系方式]”（与电话相同） | 微信、微信号、WeChat、QQ、QQ号、WhatsApp、Skype、Telegram、LINE、钉钉 | 标签后 3～40 个非空白字符（字母、数字、`_ . - +`），到空白或中文标点为止 | 不能唯一还原；真实值写入对照表“[联系方式]#n”供出口复检第 1 条比对 |
| PROJECT 项目名称 | 按对象编号“项目\_n” | 项目名称、工程名称、项目全称、Project Name、Project Title | 标签后到行尾，或遇到下一个信息标签（同人名、地址的截断规则）、`；;。` 为止；2～60 字；值为“见附件、另行约定、待定、同上”等时不替换 | 可唯一还原 |

* `REDACTION_CATEGORIES` 增加 IM、PROJECT；迁移 0009 放宽 redaction\_items、redaction\_suspects 的类别约束（降级时删除这两类的行，与 0003 的做法相同）。
* `placeholders.FIXED["IM"] = "[联系方式]"`，`_NUMBERED["PROJECT"] = "项目_{}"`；`CHAT_LABELS` 增加 IM“联系方式”、PROJECT“项目”。
* 9.3 识别顺序：IM 与 REFNO、IDNO 一起在第 4 步；PROJECT 在第 7 步（地址）之后。
* 前端：`REDACTION_KIND` 中 IM 归 contact，PROJECT 归 other（V2-B-07 再调整颜色分组）。

### 1.5 测试用例

| # | 输入 | 期望 |
|-|-|-|
| 1 | 回归样本 labels\_extended | `known_gaps` 13 条全部移入 `must_replace`：COSU1234567890、LC26FICT0091、QL-2026-ZB-017、ZB2026FICT0042、QT-2026-0508 为 REFNO；E12345678、110105012345678、MA01XYQ1-5 为 IDNO；102100099996 为 SWIFT；zhang\_fiction88、88001234、fiction.trader.01 为 IM；江北市虚构医院信息化改造项目为 PROJECT |
| 2 | `报关单号：222120260000123456` | 整串替换（REFNO 或 IDNO 均可，不得原样外发） |
| 3 | `项目名称：见附件`、`项目名称：待定` | 不替换 |
| 4 | `项目名称：东川市虚构管网改造工程；工期 90 天` | 只替换到分号前 |
| 5 | 同一微信号出现两次 | 两处都是“[联系方式]”，对照表有一条“[联系方式]#n” |
| 6 | 出口复检：外发文本中混入 labels\_extended 的微信号原文 | MAP 拦截 |
| 7 | 追问 `_redact("对方微信 zhang_fiction88 说可以")` 与 `_redact("微信：zhang_fiction88")` | 后者替换为“[联系方式_Qn]”；前者无标签冒号时按现有规则不识别（不扩大） |
| 8 | 迁移 0009 升级 / 降级 | 按 1.4；模型与迁移一致 |
| 9 | 现有回归集、A-03 | 全部通过 |

## 二、外文企业形式与无标签中国地址（V2-B-02，测试先行）

**文件范围**：`backend/app/redaction/parties.py`、`backend/app/redaction/detect.py`（地址）、`backend/app/redaction/suspects.py`（如需排除重复）、对应测试、`foreign_suffixes.yaml`、`cn_address_unlabeled.yaml`（按 2.5）。

### 2.1 外文企业后缀（后置形式）

追加到 `_EN_SUFFIX`（与现有后缀相同处理：不区分大小写，前面取首字母大写的单词）：

`A.Ş.`、`A.S.`（仅当前一词为 Sanayi、Ticaret、Tic.、San. 之一或名称以 Ltd. Şti. 结尾时）、`Ltd. Şti.`、`Ltd. Sti.`、`Sp. z o.o.`、`Sp. z o. o.`、`s.r.o.`、`a.s.`、`Kft.`、`Zrt.`、`Nyrt.`、`S.R.L.`、`SRL`、`SARL`、`S.A.R.L.`、`S.A.S.`、`S.p.A.`、`SpA`、`Ltda.`、`S.A. de C.V.`、`W.L.L.`、`WLL`、`Co. W.L.L.`、`GmbH & Co. KG`、`ApS`、`A/S`、`Oy`、`AB`、`ASA`、`K.K.`、`Bhd.`、`Est.`（仅当前一词为 Trading、General、Commercial 之一）。

**区分大小写、须是名称最后一个词、前面至少有 2 个首字母大写单词**的短后缀：`AB`、`ASA`、`Oy`、`SpA`、`WLL`、`SRL`、`SARL`（这些写法与普通缩写冲突较多，宁可放到疑似遗漏）。英文单词切分改为认拉丁字母扩展区（Ş、ı、ğ、ü、ö、ç、é、è 等），`_EN_WORD_OK` 同步放宽。

### 2.2 前置形式

* 俄文、独联体：`ООО`、`АО`、`ПАО`、`ЗАО`、`ОАО`、`ИП`、`ТОО`（西里尔字母）以及拉丁写法 `OOO`、`ZAO`、`OAO`、`TOO`，后接 `«…»`、`"…"` 或 `“…”` 引号中的 1～60 个字符：整段（含企业形式与引号）作为名称。
* 印尼：`PT`、`CV` 后接 2～8 个首字母大写的单词（到标点、`Tbk` 或行尾为止）。

### 2.3 国家

在信息块中找不到国家时，后缀推断国家（只用于【主体说明】）：A.Ş. / Ltd. Şti. → 土耳其；ООО 等西里尔形式 → 国家未知（俄罗斯、哈萨克斯坦等都用，不猜）；Sp. z o.o. → 波兰；s.r.o. / a.s. → 国家未知（捷克、斯洛伐克）；Kft. / Zrt. → 匈牙利；W.L.L. → 国家未知（海湾多国）；PT → 印度尼西亚；其他不推断。

### 2.4 无标签的中国地址（detect.find\_addresses 增加一条规则）

匹配：至少一级行政区（`省`、`自治区`、`市`、`自治州`、`地区`、`区`、`县`、`旗`，每级前 2～7 个汉字；“区、县”前 1～7 个）+ 道路（`路`、`街`、`大道`、`大街`、`道`、`巷`、`弄`、`胡同`，前 1～20 个汉字或数字）+ 门牌（1～5 位数字，后接`号`，可隔一个空格）+ 可选的楼栋房间（`栋 / 幢 / 座 / 号楼 / 楼 / 层 / 单元 / 室`，各最多 10 字）。整段替换为“[地址：中国]”。只有行政区没有道路门牌的（“本合同在北京市签订”）不算；已被其他规则替换的文字不再识别。

### 2.5 测试用例

| # | 输入 | 期望 |
|-|-|-|
| 1 | 回归样本 foreign\_suffixes | `known_gaps` 9 条全部移入 `must_replace`（ORG）；must\_keep 通过 |
| 2 | 回归样本 cn\_address\_unlabeled | 两条地址移入 `must_replace`（ADDR，代号“[地址：中国]”）；must\_keep 两条通过 |
| 3 | `Buyer: Anadolu Fictive Kimya Sanayi ve Ticaret A.Ş.` | 【主体说明】国家为土耳其 |
| 4 | `The AB test result` / `see section AB` | 不识别 |
| 5 | `Partner: Nordic Fictive Systems AB` | 识别（前面 3 个大写单词、AB 在末尾） |
| 6 | `ООО «Ромашка Фиктив»` 在追问中 | 替换为追问代号 |
| 7 | `货物运至青岛港` / `在江苏省南京市签订` | 不替换 |
| 8 | `地址：江苏省南京市江宁区虚构路 88 号` | 仍由现有标签规则替换，结果与 V2-B-02 之前相同 |
| 9 | 现有回归集、A-03 | 全部通过 |

## 三、字号派生、姓 + 称谓、叙述人名（V2-B-03，测试先行）

**文件范围**：`backend/app/redaction/parties.py`（字号派生）、`backend/app/redaction/detect.py`（人名）、`backend/app/redaction/suspects.py`（叙述人名）、`backend/app/redaction/engine.py`（接入）、`backend/app/chat/service.py`（接入）、`backend/resources/cn_surnames.txt`（新）、`backend/resources/industry_words.txt`（新）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0010_suspect_reasons.py`（新，编号按 V2Q-31）、对应测试、`names_derived_persons.yaml`（按 3.6）。

### 3.1 主体字号派生

对每个以中文企业后缀结尾的主体名称（签约方与正文企业名称，含我方）：

1. 去掉末尾的企业后缀（有限公司、股份有限公司等，含分支机构后缀）得到“主体部分”，如“澄川虚构信息技术”。
2. 去掉开头的地名（countries.yaml 中的中国省、市、港口名，以及“华东、华南、华北、华中、西南、西北、东北”），再去掉末尾的行业词（`industry_words.txt`：信息技术、技术、科技、新材料、材料、石化、化工、能源、贸易、实业、工程、建设、建筑、物流、电子、机械、医药、生物、软件、网络、电气、环保、投资、咨询、服务、供应链、国际、集团等，长的优先，可连续去掉多个），得到“字号”，如“澄川虚构”。
3. 派生别名：主体部分（≥ 4 个汉字时）与字号。字号 ≥ 3 个汉字时自动替换；2 个汉字的字号不自动替换，在正文中出现时列为疑似遗漏（原因 `derived_name`）。字号等于常用词（`industry_words.txt` 中的词、`common_terms.txt` 的中文词条、“中国、国际、东方、中华”等）时不派生。
4. 派生别名与签约方的简称（9.3 第 3 步）一样归入该主体，使用同一代号；不写入已记住的名称（只有完整名称写入）。

### 3.2 姓 + 称谓（自动替换为人名）

* `cn_surnames.txt`：常见单姓约 500 个、复姓约 80 个（欧阳、司马、上官等）。
* 称谓（长的优先）：总经理、副总经理、董事长、总监、经理、主任、主管、总工、工程师、律师、会计、老师、先生、女士、小姐、局长、处长、科长、院长、校长、书记、部长、总、工。
* 规则：`{姓}{称谓}`，前面是句首、标点、空白或“请、由、与、和、及、致、向、给、找、跟、同、经、抄送、联系、通知、委托、授权”，后面是标点、空白、句尾或任意汉字；整段（姓 + 称谓）替换为人员代号。前面是其他汉字时不算（“副总经理”中的“副”不是姓）。“高工、全总、常主任”等歧义写法照规则处理（宁可多替换）。
* 英文：`Mr. / Mrs. / Ms. / Dr. / Prof.` 后接 1～3 个首字母大写的单词，**只替换姓名部分**，称谓保留。

### 3.3 叙述中的人名（列为疑似遗漏）

`{触发词}{姓}{1～2 个汉字}{结束}`：触发词为“与、和、由、请、致、联系、经、向、给、找、跟、同、派、委托、授权、通知、抄送”；结束为标点、空白、句尾，或“沟通、协商、确认、同意、说、称、表示、负责、签字、签署、联系、提出、认为、回复、反馈、处理、商定”之一。命中的“{姓}{1～2 字}”列为疑似遗漏，原因 `person_like`；姓 + 名恰好是 countries.yaml 中的地名（张家港、常州等）时不列。

### 3.4 迁移 0010

`redaction_suspects.reason` 约束增加 `derived_name`、`person_like`；`redaction_items.detected_by` 增加 `derived`；降级时删除这些行后恢复约束。前端疑似遗漏的原因文字：“名称简写”“疑似人名”。

### 3.5 追问

chat `_redact` 第 3 步之后对本任务对照表中的主体做同样的字号派生（派生别名映射到已有代号）；第 4 步加入 3.2；疑似遗漏检查（R4-04）自动包含 3.1 第 3 条与 3.3。

### 3.6 测试用例

| # | 输入 | 期望 |
|-|-|-|
| 1 | 回归样本 names\_derived\_persons | `known_gaps` 移入 `must_replace`：澄川虚构信息技术（ORG，标注文字写“澄川虚构信息技术”）、澄川虚构（ORG，标注写“澄川虚构”）、张经理（PERSON）、李总（PERSON）、Carter Fictive（PERSON）；追加 `must_suspect`：王建国；must\_keep 三条通过 |
| 2 | 乙方“虚构科技有限公司”，正文“虚构的方案” | 字号“虚构”只有 2 字：正文中的“虚构”列为疑似遗漏（derived\_name），不自动替换 |
| 3 | 乙方“东方虚构国际贸易有限公司”，正文“东方虚构的报价”“东方红” | 依次去掉行业词“贸易”“国际”得到字号“东方虚构”（“东方”不在地名表中，不去掉）；“东方虚构”替换为乙方代号，“东方红”不变 |
| 4 | “副总经理负责” | 不识别人名 |
| 5 | “请欧阳总监确认” | “欧阳总监”替换为人员代号 |
| 6 | “Dear Dr. Lena Fictive,” | 替换“Lena Fictive”，保留“Dr.” |
| 7 | “与张家港港务局联系” | 不列“张家港” |
| 8 | 派生别名 | 不写入已记住的名称（确认发送后 redaction\_terms 只有完整名称） |
| 9 | 追问“澄川虚构的报价是否偏高？”（本任务乙方为澄川虚构信息技术有限公司） | “澄川虚构”替换为乙方已有代号 |
| 10 | 迁移 0010 | 升级 / 降级；模型与迁移一致 |
| 11 | 现有回归集、A-03 | 全部通过（如有原样本因字号派生而多替换，导致 must\_keep 失败，停下报告，不改样本） |

## 四、我方主体预置与甲乙方按立场命名（V2-B-04，测试先行）

**文件范围**：`backend/app/admin/redaction.py`、`backend/app/redaction/parties.py`、`backend/app/redaction/engine.py`、`backend/app/redaction/outbound.py`、`backend/app/redaction/placeholders.py`、对应测试。前端在 V2-B-07。

### 4.1 我方主体预置

* 不新建表：管理员在“设置 → 脱敏 → 我方主体”中录入的名称，写入 `redaction_terms`（kind=learned，category=ORG，is\_our\_party=true，created\_by=管理员）。接口：`GET /admin/our-parties`（只列 is\_our\_party=true 的已记住名称）、`POST /admin/our-parties {name}`、`DELETE /admin/our-parties/{id}`（等同删除该已记住名称）。保存时照常做 TERM\_IN\_PROMPT 检查（Q-108）。
* 预选我方（`_apply_ours`）沿用“已记住为我方、且在本文件中出现的名称优先”；同一文件中出现多个我方名称（母公司与子公司）时，**签约方信息块中**的那一个为我方，都在或都不在信息块中时不预选。

### 4.2 签约方标签带角色说明

`甲方（采购方）：`、`乙方（供方）：` 这类括号说明含“采购方、买方、需方、发包人、委托方、承租方”时，该方的规范标签记为买方；含“供方、卖方、供应商、承包人、受托方、出租方”时记为卖方；其他说明不变。

### 4.3 甲乙方按立场命名对方

用户确认（或预选出）我方以后：若我方的规范标签为甲方或乙方，且另一方（乙方或甲方）恰好一个，则另一方的代号角色按立场取：采购 → 供应商，销售 → 客户，其他 → 公司（不变）；`role_label` 仍为“甲方 / 乙方”。【主体说明】中我方写“我方=甲方（采购方）”或“我方=乙方（销售方）”（立场为其他时只写“我方=甲方”），对方写“供应商\_A：乙方，中国”。丙方及其他主体不变。

### 4.4 测试用例

| # | 输入 | 期望 |
|-|-|-|
| 1 | 预置我方名称后，上传甲乙方合同（我方为甲方），立场采购 | 解析后甲方预选为我方；乙方代号“供应商\_A”；主体说明“我方=甲方（采购方）；供应商\_A：乙方，……” |
| 2 | 同上，立场销售 | 乙方代号“客户\_A”，主体说明“我方=甲方（销售方）” |
| 3 | 立场其他 | 乙方代号“公司\_A”，主体说明“我方=甲方” |
| 4 | `甲方（采购方）：…有限公司`，未预置我方，立场采购 | 甲方规范标签为买方，按立场预选为我方 |
| 5 | 同一文件中母公司在信息块、子公司在正文 | 预选母公司 |
| 6 | 我方主体接口 | 普通用户 403；保存含固定提示词中词语的名称 422 TERM\_IN\_PROMPT |
| 7 | 重新审核改立场 | 对方代号按新立场重算 |
| 8 | 现有回归集、A-03 | 全部通过 |

## 五、撤销误识别（V2-B-05，测试先行）

**文件范围**：`backend/app/db/models.py`、`backend/app/db/alembic/versions/0011_redaction_exclusions.py`（新，编号按 V2Q-31）、`backend/app/redaction/engine.py`、`backend/app/redaction/router.py`、`backend/app/audit/service.py`（只追加动作）、`backend/app/tasks/service.py`（只改重新审核的补充说明清理分支，见第 6 条，V2Q-35）、对应测试。

1. **可撤销的范围**：本任务中自动识别的人名（detected\_by=person，含 3.2）和本任务的用户词条（detected\_by=user，任何类别）。签约方、公司名称、已记住的名称、自定义敏感词、账号、证件号、电话、邮箱、金额、地址、实体识别结果都**不能**撤销。
2. **接口**：`POST /tasks/{id}/redaction/revoke {loc, char_start, char_end}`，位置须正好是一个可撤销替换项的范围，否则 422 `NOT_REVOCABLE`；只在 awaiting\_confirm 状态（与手动补充共用任务行锁）。
3. **存储**：新表 `redaction_exclusions`（id、task\_id 外键级联、loc varchar(64) NOT NULL（撤销时的位置：NOTE 或正文节点 loc，不加密，V2Q-35）、text\_enc 加密的原文、category、created\_by、created\_at）。用户词条的撤销直接删除对应 detected\_by=user 的行，不写排除表。
4. **重算**：engine 读入本任务的排除项；人名识别命中的文字与某个排除项规范形式相同时不替换（全文生效），也不列为疑似遗漏；排除的文字不写入已记住的名称。出口复检不变（文字不在对照表中，复检第 2 条的格式重扫照常）。
5. **审计**：写 `redaction_revoke`，detail 只含 `{category}`。
6. 修改补充说明时，loc=NOTE 的排除项与用户词条一起删除（同 R4-03 4.3）：在 `tasks/service.py` 重新审核时补充说明实际变化的既有清理分支中，同一事务删除 task\_id 相同且 loc=NOTE 的排除项；正文来源的排除项保留。排除项全文生效，删除后正文中同名文字恢复替换（偏安全）。保留期清理不动排除表（与对照表同样保留）。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 自动识别的人名“周示例”撤销 | 全文不再替换“周示例”，不列疑似遗漏；审计一条，detail 无文字 |
| 2 | 撤销签约方名称、账号、金额 | 422 NOT\_REVOCABLE |
| 3 | 撤销自己手动补充的词 | 该用户词条删除，重算后不再替换 |
| 4 | 撤销后确认发送 | 外发请求含“周示例”原文（用户的选择）；redaction\_terms 没有该名称 |
| 5 | 他人任务 | 404 |
| 6 | 迁移 0011 | 升级 / 降级；模型与迁移一致 |
| 7 | 在补充说明中撤销“周示例”，重新审核时修改补充说明 | loc=NOTE 的排除项被删除，“周示例”重新被替换；正文来源的排除项保留 |
| 8 | 重新审核，补充说明未改 | 排除项全部保留 |

## 六、本地实体识别模型（V2-B-06a 选型、V2-B-06 接入）

### 6.1 选型实测（V2-B-06a，Opus，需用户同意下载模型；原方案，结论见 6.2）

* **候选**（许可在实测时逐个核实，只用允许商用的）：
  1. `urchade/gliner_multi-v2.1`（Apache 2.0，mDeBERTa-v3-base，可直接指定“人名、公司、机构、地址”等标签；旧版本为非商用许可，不得使用；中文效果与 ONNX 导出待验证）；
  2. 在 CLUENER2020 / MSRA 等中文数据集上训练、许可明确允许商用的 BERT / RoBERTa 实体识别模型 1～2 个；
  3. 可选：一个多语种 token 分类模型（覆盖英文、土耳其文、俄文名称）。
* **评测集**：现有回归集 + 本批四份样本 + 新写的 6～8 份虚构叙述性样本（函件、会议纪要、索赔函、法律文书片段、英文邮件），每份标注人名、机构名、地址。
* **指标与通过线**：叙述文本中未被规则识别的人名、机构名，高 + 中置信合计召回 ≥ 90%；高置信档在全部 `must_keep` 上误替换 0；10,000 字的处理时间 ≤ 3 秒（2 个 vCPU）；单进程内存增加 ≤ 1 GB；镜像增加 ≤ 1.5 GB；完全离线可运行（`--network none`）。
* **下载与运行**：模型只在开发机下载（下载前把文件名、来源、大小告诉用户并取得同意），放在独立目录，在 `--network none` 的容器中评测；不在服务器上下载。
* **产出**：`docs/v2/reports/V2-B-ner-selection.md`（各候选的指标、许可、文件大小与 SHA-256、推荐与阈值）；SPEC-V2 B 部分第 3 节、CLAUDE.md 禁用清单的修改稿（写明模型名称与许可）。经用户确认后 V2-B-06 才能开工。

### 6.2 选型结论（V2-B-06a 审核，用户 2026-10-24 确认，V2Q-37）

实测报告见 [V2-B-ner-selection.md](../reports/V2-B-ner-selection.md)。按 6.1 原门槛没有候选全部通过；用户提供目标硬件（28 vCPU、38 GiB 内存、balloon 下限 16 GiB）并要求不为轻量化牺牲识别效果，资源门槛改为 6.4，识别效果门槛不变。

* **选定**：`urchade/gliner_multi-v2.1`，固定提交 `443d26d654e0324125a96bebd8e796c14ff2efe6`，Apache-2.0；分词文件来自 `microsoft/mdeberta-v3-base` 提交 `a0484667b22365f84929a935b5e50a51f71f159d`，MIT。产品使用 V2-B-06a 用 GLiNER 0.2.24 官方接口从上述固定权重导出的 **FP32 ONNX**（opset 19），**不量化**（默认 INT8 召回降到 7/32）。
* **产品文件清单**（6 个，构建时放入 `backend/models/ner/`，不进仓库）：

  | 文件 | 字节数 | SHA-256 |
  |-|-:|-|
  | model.onnx | 1,157,108,161 | `d3f51a308a876e09209321cc466dffcc4b1ca8f4877144d24b08ae3f7c8dabd0` |
  | tokenizer.json | 16,351,316 | `87208f39b9e81f180629d45da55d7a24cf1e011884552d6d5096c92e0a639d39` |
  | tokenizer\_config.json | 20,227 | `928d015db8ce40c131beaf323a25ab913c8d7b7d20baf997bf42e0f6d3cd2f8b` |
  | special\_tokens\_map.json | 286 | `9463f61e1b109a8eb4688b829260d7c6b1e6dff04c98ff7269bb89e2b92369b9` |
  | added\_tokens.json | 86 | `030e747c4ca7992a3ac794c6fda9919352c88ae722e85178217cd083b450078d` |
  | gliner\_config.json | 453 | `a947b4597e76e9f2baf3c9d33b78ad994ee2d8a2efdbfbafc580fb84bc441aea` |

* **推理参数**（与 06a 实测一致，ner.py 常量）：标签 `person` → PERSON、`organization` → ORG、`street address` → ADDR；预切分为“单个汉字 / 拉丁字母词（含 `'’-` 连接）/ 数字 / 其他非空白字符各为一个词”；320 字符窗、步长 288，重叠部分同一区间取最高分；以 0.01 为下限做 flat 解码后再分档；**中阈值 0.4、高阈值 0.95**。
* **已知限制**：span 最长 12 个词（gliner\_config `max_width`），超过 12 个汉字的机构名只能靠规则；完整残余地址 0/2，地址仍以规则为主；英文人名 `Nora Vale` 漏识别。这些不降低 B-08“漏掉数必须为 0”的要求（只能列为疑似遗漏的算“列为疑似遗漏”）。

### 6.3 接入（V2-B-06，测试先行）

**文件范围**：`backend/app/redaction/ner.py`（新）、`backend/app/redaction/engine.py`、`backend/app/chat/service.py`、`backend/app/main.py` 与 `backend/app/worker.py`（只加启动自检）、`backend/resources/ner_stopwords.txt`（新）、`backend/models/ner/MANIFEST.sha256`（新，入仓，只含 6.2 清单的文件名与 SHA-256）、`.gitignore`（忽略 `backend/models/ner/` 下除 MANIFEST 外的文件）、`backend/Dockerfile`（模型文件与校验）、`backend/requirements.txt`（只加 `tokenizers`，固定版本）、`deploy/scripts/build_release.sh`（只加模型目录参数与校验）、`backend/app/db/models.py`、`backend/app/db/alembic/versions/0012_ner_source.py`（新，编号按 V2Q-31）、`backend/tests/fixtures/ner/`（新：对拍参考、新留出集）、对应测试。

* **接口**：`ner.recognize(text: str) -> list[Entity]`，`Entity(start, end, label: "PERSON" | "ORG" | "ADDR", score: float)`；进程内首次使用时加载一次并复用；`ner.check()` 在 app 启动（lifespan）和 worker 进入主循环前调用，模型文件缺失、SHA-256 不符或加载失败时拒绝启动（同主密钥检查，Q-28）。app 启动时只校验文件与 SHA-256，第一次追问时才加载；worker 启动时加载。
* **推理实现**：只用 `onnxruntime` + `numpy` + `tokenizers`（Apache-2.0）自写 GLiNER `markerV0` 的前处理（预切分、`<<ENT>>` / `<<SEP>>` 标签提示、子词首 token 对齐、span 索引与掩码）与后处理（sigmoid、flat 贪心去重叠、映射回字符位置）。**产品镜像不得安装 torch、transformers、gliner、sentencepiece**。会话选项：worker `intra_op_num_threads=8`，app 每进程 4，`inter_op_num_threads=1`，关闭 ORT 遥测。允许把多个窗口合成一批（每批 ≤ 8 窗），对拍仍须通过。
* **对拍**：把 06a 的 `gliner-fp32-characters.predictions.json` 中分数 ≥ 0.3 的结果裁剪为 `backend/tests/fixtures/ner/reference_predictions.json`（只含节点 id、起止、类别、分数，不含文字；节点文字由测试按 06a 的 `prepare_corpus.py` 同法重建）。实现对 391 个节点逐条比对：分数 ≥ 0.3 的（起、止、类别）集合完全一致，分数差 ≤ 1e-4。对不上时停下汇报，不得改阈值或样本凑数。
* **结果过滤**（高、中两档都适用，只去掉模型结果，不影响规则）：整段等于 `ner_stopwords.txt` 中的词（合同角色：甲方、乙方、买方、卖方、我方、贵司、本公司、Buyer、Seller、Supplier、Consignee 等；称谓：Mr.、Ms.、经理、女士等；通用名词：银行、Bank、Address、Beneficiary Bank、仲裁委员会、中华人民共和国等）；去掉首尾空白后少于 2 个字符；只由数字、空白和 `-/.` 组成；ADDR 结果中不含任何字母或汉字。词表按 06a 实测的误报整理，可增不可删。
* **接入位置**：engine 在 9.3 第 1～7 步与新规则之后、疑似遗漏之前，对每个节点（含 NOTE）**未被替换的文字**（已替换区间以等长空白遮盖，保持位置）调用；score ≥ 高阈值的自动替换（PERSON → 人员代号；ORG → 作为主体登记，角色“其他”；ADDR → [地址：国家]），detected\_by=`ner`；中阈值 ≤ score < 高阈值的列为疑似遗漏，原因 `ner`；低于中阈值忽略。
* **追问**：chat `_redact` 同样调用；高置信替换为追问代号，中置信并入 R4-04 的疑似遗漏。
* **只增不减**：模型结果不撤销、不改变任何规则替换；实体识别的替换不能撤销（B6）。
* **离线**：模型文件不进仓库、运行时不联网不下载。构建上下文 `backend/models/ner/` 由开发机从 06a 证据目录复制；runtime 与 test 两个 stage 都 COPY 模型并按 `MANIFEST.sha256` 校验，缺文件或哈希不符即构建失败。`build_release.sh` 增加 `--ner-models <目录>`：复制到 clone 的 `backend/models/ner/` 并校验后再构建，未提供时拒绝构建。禁用遥测（同 Q-64）；T-20 扫描照常通过。
* **迁移 0012**：detected\_by 增加 `ner`，suspects.reason 增加 `ner`。
* **并发**：模型调用受“解析 2 个并发”约束（V2-C-01 之前仍受现有 WORKER\_CONCURRENCY 约束），不得放大现有 worker / app 并发。

| # | 场景 | 期望 |
|-|-|-|
| 1 | 6.1 的叙述性样本 | 高 + 中置信合计召回 ≥ 90%；高置信在 must\_keep 上零误替换（与选型报告一致） |
| 2 | 模型文件被删除或改动 | app、worker 启动失败并给出原因（不含文件内容） |
| 3 | 规则已替换的文字 | 模型不再重复识别，替换结果与 V2-B-06 之前相同 |
| 4 | `--network none` 容器 | 解析、追问可用 |
| 5 | 追问“经与王建国沟通……” | 王建国被替换或列为疑似遗漏（取决于置信度） |
| 6 | 迁移 0012 | 升级 / 降级；模型与迁移一致 |
| 7 | 现有回归集、A-03、T-20 | 全部通过 |
| 8 | 对拍（391 节点） | 与 06a FP32 结果一致（见“对拍”） |
| 9 | 新留出集 `fixtures/ner/holdout/`：≥ 8 份虚构叙述、≥ 40 个人名 + 机构名，含中英文；**样本与标注单独提交后才运行模型**，提交顺序可查 | 高 + 中置信合计召回 ≥ 90%；高置信在 must\_keep 上零误替换；阈值不得因此调整 |
| 10 | 中置信误报：现有 golden + 6.1 叙述共 391 节点 | 过滤后不属于应识别实体的中 / 高置信结果 ≤ 20 条（06a 为 80 条） |
| 11 | runtime 镜像 | `torch`、`transformers`、`gliner`、`sentencepiece` 均未安装；镜像比 V2-B-05 的 runtime 增加 ≤ 1.5 GB（按 `docker history` 各层解压大小之和的差值；containerd 镜像存储下 `image inspect` Size 会重复计数，不用，V2Q-39） |
| 12 | 开发机预检：`--cpus 8`、worker 线程配置 | 1 万字（同 06a 拼接方法）预热后中位数 ≤ 10 秒；单进程峰值内存增加 ≤ 2.5 GB |
| 13 | 撤销“周示例”后重新计算，模型对其给出高或中置信结果 | 不替换、不列疑似遗漏（与第五节一致，V2Q-39） |
| 14 | 原 must\_suspect 中被模型高置信替换的两条（inquiry 的 Harbor Fictive Labs、persons\_narrative 的林虚） | 仍列疑似，或被 detected\_by=ner、同类别且完整覆盖的替换项替换；只此两条例外（V2Q-39） |

### 6.4 资源门槛与整机容量测试（用户 2026-10-24 确认，V2Q-37、V2Q-38；时延门槛 2026-10-25 按 V2Q-56 方案丙修订）

B-06 只做 6.3 第 11、12 条的开发机预检；以下整机测试放在 **V2-B-08**，在 **172.16.11.252** 上做（用户指定，V2Q-38）。

| 项目 | 门槛 |
|-|-|
| 解析耗时 | 1 万字残余文字的模型识别（worker 配置，热态）≤ 15 秒（最大值）；20 页合同单任务整体解析时间比 V2-B-05 代码增加 ≤ 30 秒（最大值）；两个 20 页任务并发时，每个任务比 V2-B-05 单任务增加 ≤ 60 秒（最大值），两个任务均完成 |
| 追问耗时 | 模型已加载时（app 进程配置，单请求）：500 字问题的模型识别 ≤ 3 秒（p95），1,000 字（追问上限）≤ 6 秒（p95）；追问本地整体（`chat._redact`，含规则）≤ 对应模型识别门槛 + 0.2 秒；第一次追问（含加载）≤ 15 秒，app 启动校验时间单列记录 |
| 追问接口压力 | 真实 HTTP 接口 + 模拟网关、两个 uvicorn 进程，在下面的整机压力场景中：从收到请求到交给模拟网关 ≤ 8 秒（p95）；全部请求在 2 分钟内完成，无超时、无 5xx |
| 单进程内存 | 加载模型后峰值增加 ≤ 2.5 GB |
| 整机压力场景 | 2 个解析任务并行 + app 两个进程均已加载且同时追问 + OCR 同时工作：测试套件峰值总内存 ≤ 12 GiB；无交换、无 OOM；各请求不超时 |
| balloon 下限 | 压力场景测试套件峰值 + 同时刻正式服务的实际内存 ≤ 16 GiB |
| 镜像 | 增加 ≤ 1.5 GB（口径同 6.3 第 11 条） |
| 离线与自检 | 测试套件 app / worker 在断网网络下运行；模型文件改动后拒绝启动 |

**时延门槛的依据与统计方式**（V2Q-56）：内网使用，界面已有等待提示（解析“正在解析文件，请稍候……”并轮询；追问“正在等待回复，最长约 2 分钟”），时延门槛按“不超时、不阻塞、等待有提示”的上限设定，不另加界面功能；识别效果、内存、离线与安全门槛不变，worker 8 / app 每进程 4 线程不变。已有实测已满足新门槛的项沿用 V2-B-test.md 0B/0C 的样本、不重测（500 字纯 NER 与整体 2.18～2.22 秒、万字最大 12.48 秒、20 页单任务增加最大 26.68 秒、首次追问 11.6～12.4 秒）；只补测从未测过的项：1,000 字追问为 1 个新进程冷 1 次后热 10 次取最大值，接口压力与双任务并发按下面的整机压力场景测 1 轮，两个追问客户端各 ≥ 14 次、四路重叠 ≥ 50 秒，取 p95 与最大值。测量期间记录 steal，超过 5% 的结果作废重测。剩余优化评估见 [V2-B-perf-draft.md](V2-B-perf-draft.md)。

**在正式服务器上测试的条件**（V2Q-38）：

1. 每次开测前把时间段、预计时长、要启动的容器告诉用户，取得同意后才开始；选在用户指定的低峰时段。
2. 测试套件用独立的 compose 项目名、独立的网络、数据卷和端口，使用测试用主密钥与虚构样本；不停、不重启、不升级正式服务，不读写正式数据库、数据卷、主密钥和备份。
3. 测试套件的容器设置内存上限（合计 12 GiB）；监测宿主可用内存，低于 4 GiB 或正式服务出现异常时立即停止测试套件。
4. 测试结束后删除测试容器、网络、数据卷和测试镜像，并记录删除结果；结果写入 V2-B-08 报告。

## 七、前端（V2-B-07）

**文件范围**：`frontend/src/utils/labels.ts`、`frontend/src/styles/theme.css`（只追加 `--si-k-amount*`）、`frontend/src/components/RedactionCompare.vue`、`frontend/src/components/ManualRedactDialog.vue`、`frontend/src/components/SuspectList.vue`、`frontend/src/views/settings/Redaction.vue`、`frontend/src/api/redaction.ts`、`frontend/src/api/admin.ts`，以及后端 `backend/app/redaction/engine.py` 中 `USER_CATEGORIES` 与手动补充类别处理、`backend/app/redaction/router.py` 的类别校验和对应测试。

1. **图例 7 组**：公司银行 / 人名 / 联系方式（电话、邮箱、网址、即时通信） / 账号编号（账号、SWIFT 与银行代码、证件号、编号） / 地址邮编 / **金额（金额、单价、数量，新颜色）** / 其他（敏感词、项目）。
2. **手动补充与疑似遗漏“替换”的类别**扩为 10 种：公司、人名、联系方式（IM）、账号、证件号、编号、地址、金额、项目、其他。后端 `USER_CATEGORIES` 同步：金额须能解析为数字（否则 422 `INVALID_AMOUNT`），代号与自动识别规则相同（同值同代号）；地址按 find\_country 取国家；已记住的名称仍只写公司、人名。
3. **撤销**：对照区与原文中可撤销的替换（人名、本任务手动词条）悬停时显示“撤销（不是敏感信息）”，二次确认后调用 revoke 接口并刷新。
4. **我方主体**：设置 → 脱敏增加“我方主体”列表（名称、记录人、时间，可新增、删除）。
5. **新原因文字**：derived\_name“名称简写”、person\_like“疑似人名”、ner“模型识别”。
6. 完成标准：`npm --prefix frontend run build` 通过；浏览器检查在 V2-B-08。

## 八、集中测试（V2-B-08）

**产出**：`docs/v2/reports/V2-B-test.md`（新）。

1. 整仓挂载 Docker 全量测试通过；`--network none` 容器中 OCR、实体识别、T-20 通过。
2. **按类别统计召回率**：回归集全部样本与 6.1 的叙述性样本，逐类别列出应替换数、已替换数、列为疑似遗漏数、漏掉数；漏掉数必须为 0（叙述性样本中只能列为疑似遗漏的，算“列为疑似遗漏”）。
3. A-03：外发记录真实值零命中。
4. 用本轮讨论中的虚构探测句（机构签约方、无币种金额、叙述人名、字号简称、英文称谓、外文企业形式、无标签地址、微信号）经真实接口 + 模拟网关走一遍，逐句写结果。
5. 浏览器清单（四种宽度）：图例 7 组；10 种手动类别；撤销的悬停、确认与刷新；我方主体设置；新原因文字；追问中的模型识别疑似遗漏弹窗。
6. 内存与耗时：worker、app 进程在处理 20 页合同时的常驻内存与解析耗时，与 V2-A 结束时对比；并按 6.4 在 172.16.11.252 上做整机容量测试（遵守 6.4 的测试条件），逐项写结果。

## 九、批次审核（V2-B-09，Opus）

核对测试用例与样本未被删改、“只增不减”（第〇节第 4 条）、文件范围、日志、A-06、离线；审阅 V2-B-08 报告；汇报用户，确认后进入 V2-C。

## 十、新增待确认问题

见 docs/v2/questions/V2-B.md（V2Q-10～V2Q-16 等）。

## 十一、金额写法补全与手动金额带币种（V2-B-10，测试先行，V2Q-57）

2026-10-25 · Opus 5.5（V2-B-09 审核后追加） · 用户已确认（2026-10-25）

**起因**：V2-B-09 审核中实测，中文合同常见的几种金额写法没有被第 5 步识别（“1000块”“1千元”“1000余元”“1,000.00（元）”“人民币一千元”“15万欧元”“日元 56,000”“美金 7,800”），“港币1,000元”的币种被认成人民币。出口复检重扫也用 `find_amounts`，所以这些金额会原样外发。另外，确认页手动标记“金额”只接受纯数字（V2Q-42），选中带“¥”“元”的文字会被拒绝。本节一并处理，V2Q-57 取代 V2Q-42 的第 1、2 条。

**文件范围**：`backend/app/redaction/detect.py`（只改第 5 步金额：`_CURRENCY`、`_PREFIX`、`_SUFFIX` 及新增的辅助正则，`find_amounts`）、`backend/app/redaction/engine.py`（只改 `_manual_amount`、`compute` 中用户金额候选的处理、`_add_term` 的金额校验）、`backend/tests/test_v2b_amounts.py`（新）、`backend/tests/fixtures/redaction_golden/amounts_cn_forms.yaml`（按 11.4 转正）。另按 V2Q-58（用户 2026-10-25 批准）只做两处既有测试同步：`test_redaction_engine.py::test_fill_brackets_patterns` 去掉 `人民币【222063】元` 断言的 `* 2`；`test_a03_outbound.py` 只对 amounts\_cn\_forms 的 `15` 标注先排除完整的 `[C-15]` 再检查，并补反例。不改 recheck.py、`find_bare_amounts`、前端、迁移（AMOUNT 类别已有）。

**开工前已写入**：回归样本 `amounts_cn_forms.txt / .yaml`（11 处待修项以 known\_gaps 登记，当前测试通过）。**样本 .txt 与已有标注文字不得改动。**

### 11.1 自动识别补全（`find_amounts`，只替换数字，币种与单位文字保留）

| # | 写法 | 规则 | 币种 / 数值 |
|-|-|-|-|
| 1 | 前置币种：日元、英镑、港币、港元、美金 | 加入 `_PREFIX`；“美金”“港元”“港币”也加入 `_CURRENCY` 与后置单位（`7800美金`） | JPY、GBP、HKD、USD |
| 2 | 前置与后置同时出现：`港币1,000元`、`美金1,000元` | 同一数字既有前置币种、又有后置“元”时以前置币种为准；同一区间只产生一个命中（`人民币1,000元` 现在会产生两个相同命中，一并去重） | HKD、USD |
| 3 | 块 / 块钱：`4870块`、`4870 块钱`、`50块整` | 数字（前一个字不是“第”、数字、字母、`_`）+ 可选空格 + “块”，其后是“钱”“整”、标点、空白或文末；“块”后紧跟其他汉字时不算（`3 块区域`、`100块砖`） | CNY |
| 4 | 量级：千 / 百万 / 千万 + 币种（`3.5千元`、`2百万美元`），以及“万 / 亿”+ 现有后置单位以外的币种（`15万欧元`、`20万港元`、`3万日元`、`1亿日元`） | 数字 + 量级 + 后置币种词；数值乘量级 | 按币种词；千 ×1,000，百万 ×1,000,000，千万 ×10,000,000 |
| 5 | 余 / 多：`2600余元`、`68万多元`、`20多万元` | 数字后、量级前或量级后可有一个“余 / 多”；只替换数字 | 数值按数字 × 量级（不加估算） |
| 6 | 单位在括号里：`9,350.00（元）`、`50(万元)`、`1,200（美元）` | 数字 + 可选空格 + 全角或半角括号，括号内只有币种词（可带量级）；只替换数字 | 按括号内的币种与量级 |
| 7 | 小写中文数字：`三万五千元整`、`两千元`、`十万元`、`一百二十万美元` | 由 一～九、两、〇、零、十、百、千、万、亿 组成，**至少含一个** 十 / 百 / 千 / 万 / 亿，后接 元 / 圆 / 后置币种词，可带“整”；整段替换（与大写金额相同）；前一个字是“第”“每”或大写 / 小写数字时不算 | 先逐字换成大写（一→壹……十→拾、百→佰、千→仟、两→贰、〇→零）再用 `chinese_upper_value`；币种按币种词，“元 / 圆”为 CNY |

**V2-B-11 审核补充（V2Q-59，用户 2026-10-25 确认）**：第 7 行的小写中文数字还须含一～九、两中的一个或以“十”开头（“单位：千元”“百元大钞”不算）；“圆”只用于第 7 行，阿拉伯数字后的“圆”不算（“Φ20圆钢”）。“3.1 USD 100”的跳过只适用于能作前置币种的后置词且前面数字无千分位、整数部分不超过 2 位；元、万元、块等后面紧跟数字时仍是金额（“支付30000元 3日内”）。

**不做**：不带币种的“1000万”“3千”（可能是数量）；“一元 / 二元 / 多元”这类不含十百千万亿的写法（“一元化”“二元结构”“多元化”）；“每百元”这类比例说法；`find_bare_amounts` 的标签与门槛不变。

**只增不减**：只新增命中，现有命中的区间、类别、币种与代号不得变化（第 2 条的去重只去掉重复的同区间命中；`港币…元` 的币种由 CNY 改为 HKD 是唯一的币种变化，代号键随之改变）。

### 11.2 手动标记金额（确认页手动补充、疑似遗漏“替换”选金额）

1. 选区去掉首尾空白后若是纯数字，按 V2Q-42 原规则（有限十进制、正负号、规范千分组、小数）。
2. 否则对选区调用 `find_amounts`：
   * 恰好一个命中（同区间去重后），且命中之外的文字只由下列成分组成：币种词与符号（`_CURRENCY` 的全部键，含“块 / 块钱”）、量级（千、万、百万、千万、亿）、余 / 多、整、约 / 计 / 共计、全角或半角括号与空白——视为有效金额；
   * 其他情况（没有命中、多个命中、夹有其他文字）返回 422 `INVALID_AMOUNT`，提示改为“金额须为数字，可带币种或单位（如 ¥1,000、1000元）”。
3. 有效时：**只替换选区内的数字部分**（与自动识别相同，币种与单位文字保留）；币种与数值取命中的币种与数值（含量级换算）；代号键与自动识别相同（同类别、同币种、同数值同代号）。纯数字选区仍不推断币种。
4. 用户词条的全文匹配按“选区原文”找出现位置，每处出现只替换其中的数字部分（与第 3 条相同的相对位置）。
5. 选区与已替换的文字重叠时，仍按现有规则返回 409 `ALREADY_REDACTED`。

### 11.3 出口复检

recheck.py 不改；它调用的 `find_amounts` 自动带上 11.1 的新写法。须验证：固定提示文字、全部内置要求、系统提示词与追问提示词模板经 `recheck` **不产生新的 AMOUNT 命中**（否则所有任务都会被拦截）；外发文本中的代号（`[金额_1]元`、`[金额_2]块`）不被当成金额。

### 11.4 测试用例

| # | 输入 | 期望 |
|-|-|-|
| 1 | 回归样本 amounts\_cn\_forms | known\_gaps 11 条全部移入 must\_replace，category 写 AMOUNT（文字不变）；must\_keep 三条通过（“一元化 / 多元化”“3 块区域”“每百元”不替换、不列疑似遗漏） |
| 2 | 11.1 表中每一行的示例 | 逐条断言命中区间（只含数字；第 7 行为整段）、类别、币种、数值 |
| 3 | `港币1,000元`、`人民币1,000元` | 各只有一个命中；币种 HKD、CNY |
| 4 | `100块砖`、`第2块`、`分为3块区域` | 无金额命中 |
| 5 | `一元化`、`二元结构`、`多元化经营`、`第十元`、`每百元收入`、`捌拾万元`（大写） | 前五个无命中；大写仍按原规则只有一个命中，数值 800000（小写规则不得在大写中间切出“万元”） |
| 6 | `1000万`、`3千`（无币种） | 无命中 |
| 7 | 外发文本 `[金额_1]元`、`[金额_12]块钱`、`[金额_3]（元）` | 无命中 |
| 8 | 固定提示文字、内置要求全文、系统 / 追问提示词模板 | `recheck` 无 AMOUNT 命中 |
| 9 | 手动标记：`¥1,000`、`1000元`、`1,000.00（元）`、`USD 2,500`、`3.5千元`、`2600余元`、`4870块钱`、`三万五千元整` | 均成功；只替换数字（第 8 个为整段）；币种与数值同自动识别；与同币种同数值的自动金额共用代号 |
| 10 | 手动标记：`约1000元左右的费用`、`1000元和2000元`、`abc`、`1,00元` | 422 `INVALID_AMOUNT` |
| 11 | 手动标记纯数字 `1000`，同文另有自动识别的 `1000元` | 手动的不推断币种，与 `1000元` 不共用代号（V2Q-42 原规则） |
| 12 | 手动标记 `1000元` 后，同文另一处出现 `1000元` | 两处都只替换数字，代号相同；还原后与原文完全一致 |
| 13 | 疑似遗漏“替换”选金额，选区为带币种文字 | 与第 9 条相同 |
| 14 | 现有回归集、A-03、`test_v2b_manual.py`、`test_redaction_engine.py`、`test_quotes.py` | 全部通过；如有原样本因新规则多替换导致 must\_keep 失败，停下报告，不改样本 |

单个任务按 TASKS-V2 第一节第 2 条只跑本任务相关测试：`test_v2b_amounts.py`、`test_redaction_golden.py`、`test_a03_outbound.py`、`test_v2b_manual.py`、`test_redaction_engine.py`、`test_quotes.py`、`test_recheck.py`。本任务结束后**加跑一次整仓挂载全量**（V2-B 已过集中测试，不再另设集中测试任务），然后停下交 Opus 审核（规则 12），审核通过后才进入 V2-C-01。

## 十二、真实样本反馈修复（V2-B-12～V2-B-15，V2Q-143～V2Q-145）

2026-10-29 · Opus 5.5（V2-D-10 之后追加） · 用户已确认（2026-10-29，含 V2Q-143～145 按建议）

**起因**：用户 2026-10-29 提供两份真实样本（中英对照的供货函 .docx；32 页英文询价与检验计划 .pdf，样本不进仓库），用当前 main 的解析与脱敏离线实测，发现四类问题：

1. 正文中未带标签的中文企业名称把前面的“我方”“现指定”一并取入（“我方××碳材料股份有限公司”“现指定××国际贸易有限公司”整段成为一个主体名称），外发文本丢掉“我方”，带前缀的写法还会写入已记住的名称。
2. 人名：“尊敬的××（全名）先生”与“DEAR MR.××（大写、点后无空格）”未替换，只列疑似遗漏；“Product Name: Calcined Needle Coke”中的 Calcined 被当成人名替换。
3. 同一家公司的中文名与英文名（拼音字号 + 英文行业词 + Co., Ltd.）分到两个代号，模型会以为文件里多了两家公司。
4. 英文为主的 PDF 产生 **735 条**疑似遗漏（相同文字去重后 174 组），大多是首字母大写的技术术语（Bulk Density、Document Title）、标准编号（ASTM D4052、GB/T 24526）、代词与角色词（We、The inspector）；第二步要求疑似遗漏全部处理才能发送，等于无法使用；真正该替换的无后缀名称（“×× Refinery”“×× Technical Office”、无后缀的英文字号）埋在其中。

**任务划分**：V2-B-12 做 12.1～12.3（不需要决定）；V2-B-13 做 12.4（依赖 V2Q-143）；V2-B-14 做 12.5（依赖 V2Q-144，需下载授权）；V2-B-15 由 Opus 审核。三个实现任务按 TASKS-V2 第一节连续完成，**V2-B-14 结束后加跑一次整仓挂载全量**（不另设集中测试），停下交 V2-B-15。V2Q-143 / 144 已确认（2026-10-29）；V2-B-13、14 下载资源前仍须向用户说明并取得同意，未同意时停在该任务。

**共同约定**：本节第〇节第 3、5、7 条照旧；“只增不减”在本节有三处收窄，均为把**不是敏感信息的文字**移出替换或疑似遗漏：12.1 名称前的边界词、12.2 第 3 条的“Product Name:”、12.3 第 2 条的标准编号与代词，各有 must\_keep 或单元测试兜住名称本身仍被替换。recheck.py 不改。

**文件范围**：
* V2-B-12：`backend/app/redaction/parties.py`（只改边界词表）、`detect.py`（只改 `find_persons` 及其常量）、`suspects.py`（只改首字母大写段与参数）、`ner.py`（只改 `keep_entity`）、`engine.py`（`compute` 传入小写词集合、`suspect_views`、`decide_suspect`）、`backend/app/redaction/router.py`（SuspectView 加字段）、`backend/app/chat/service.py`（只接入小写佐证）、`backend/resources/ner_stopwords.txt`（只追加）、`frontend/src/components/SuspectList.vue`、`ConfirmStep.vue`（只改计数）、`frontend/src/api/redaction.ts`、`backend/tests/test_v2b_feedback.py`（新）、`real_feedback_bilingual.yaml`（按 12.6 第 1 条）。
* V2-B-13：`backend/resources/hanzi_pinyin.txt`（新）、`backend/scripts/gen_hanzi_pinyin.py`（新）、`backend/app/redaction/parties.py`（新增关联函数）、`engine.py`（接入）、`chat/service.py`（接入）、`dictionaries.py`（读资源）、`test_v2b_feedback.py`（追加）。
* V2-B-14：`backend/resources/en_words.txt`（新）及来源说明、`dictionaries.py`、`suspects.py`、`backend/tests/fixtures/redaction_golden/en_spec_suspects.*`（由 Opus 开工前写入）、`test_v2b_feedback.py`（追加）。
* 均不加迁移，不改 recheck.py、外网代码与已有回归样本 .txt。

**开工前已写入**：回归样本 `real_feedback_bilingual.txt / .yaml`（虚构，结构仿照真实样本）：五个企业名称在 must\_replace 中（当前因整段覆盖而通过），三处人名以 known\_gaps 登记，“Velmora Refinery”在 must\_suspect 中；当前测试通过。**样本 .txt 与已有标注文字不得改动**，只可按各节移入 must\_replace、追加 must\_keep。

### 12.1 中文正文企业名称的起点（V2-B-12）

`parties._CN_BOUNDARY_WORDS` 增加（多字的优先匹配，沿用 `_name_start_after_boundary` 与 `_CN_BOUNDARY_PROTECTED` 机制）：

* 自称与他称：我方、我司、本司、我公司、本公司、贵方、贵司、贵公司、对方；
* 指定类动词：现指定、现委托、兹委托、特委托、指定、授权、聘请、选定。

单字“现”“特”“兹”不加（“现代”“特变”等字号会被截断）。这些词同时进入 `_EXTEND_STOP_WORDS`（现有写法自动包含）。疑似遗漏（suspects.py 以 min\_han=0 调用同一函数）随之生效。

不处理已写入的带前缀的已记住名称（不迁移数据）。

### 12.2 人名（V2-B-12，`detect.find_persons`）

1. **中文全名 + 敬称**：`{姓}{1～2 个汉字}{先生|女士|小姐}`，前面是句首、标点、空白、3.2 的前导词，或新增的前导词“尊敬的、亲爱的”；**只替换姓名部分**，敬称保留（与英文规则一致）。姓名部分在 `_CN_NOT_PERSON` 中时不算。3.2 的“姓 + 称谓”（“叶先生”）不变，3.2 的前导词也加入“尊敬的、亲爱的”。
2. **英文称谓**：称谓不区分大小写，增加 Miss；`Mr / Mrs / Ms / Miss` 后可以是“点 + 可选空白”或“空白”；`Dr / Prof` 仍须带点（无点的 Dr 可能是 Drive）。姓名为 1～3 个首字母大写的单词（全大写也算，`MR.KE MOLAN`），遇 `_EN_STOP_WORDS` 停止；只替换姓名，称谓保留。
3. **`Name:` 标签**：前一个单词（同一行，以空白相隔）是 Product、Item、Commodity、Goods、Cargo、Brand、Model、Material、Part、Article、Grade、Chemical、Trade 之一时，不当作人名标签（与现有 Project Name 的处理相同）。Company、Bank、Account 不在此列（现有行为不变）。

### 12.3 疑似遗漏：分组与过滤（V2-B-12）

1. **分组显示**：`engine.suspect_views` 把规范形式（`P.normalize`）相同的疑似遗漏合成一行，不论原因；代表行取文档顺序的第一处，原因取现有优先级最高的一个；新增字段 `occurrences`（该组条数）。`decide_suspect` 的 ignore 改为本任务中规范形式相同的**全部原因**的 pending 一并忽略（Q-70 ④原为同原因，见 V2Q-145）；replace 不变（本来就全文替换后重算）。第二步的“还有 n 条未处理”、发送前检查按**组数**计。前端 `SuspectList.vue` 在文字后显示“共 n 处”（n > 1 时），`ConfirmStep.vue` 的计数改为组数；`frontend/src/api/redaction.ts` 类型加 `occurrences`。数据库不变（仍逐条存储）。
2. **实体识别结果过滤**（`ner.keep_entity`，两档都生效；`cache_context` 已包含 ner.py 与停用词文件，缓存自动失效）：
   * 只由小写拉丁字母、空白与连字符组成的（approved suppliers、inspector）；
   * 标准编号：`GB`、`GB/T`、`GB/Z`、`YB/T`、`HG/T`、`SH/T`、`JB/T`、`T/` + 大写字母、`ASTM`、`ISO`、`IEC`、`EN`、`DIN`、`JIS`、`BS`、`API` 单独出现，或其后跟编号（`ASTM D4052`、`GB/T 24526`、`T/ZGTS 002-2022`）；
   * `ner_stopwords.txt` 追加：we、us、our、you、your、your company、our company、the buyer、the seller、buyer、seller、party、parties、contractor、manufacturer、supplier、inspector、the inspector、inspection company（只增不删）。
3. **首字母大写段**（`suspects._capitalized_candidates`）：
   * 开头的单字母编号（`A.`、`B.`、`(a)`）去掉后再判断；
   * **文内小写佐证**：段中每个单词（去掉所有格与连字符两端）都在本次识别的全部正文节点中以全小写形式单独出现过时，不列（“Bulk Density”而正文另有“bulk density”）。engine 在 `compute` 中一次性收集小写词集合传入；追问入口只用问题文本本身。
4. 实测（真实 PDF，原型脚本，不进仓库）：735 条 → 174 组 → 第 2、3 条后约 106 组。剩余的主要是从不以小写出现的技术术语，由 12.5 处理。

### 12.4 中英文同一主体（V2-B-13，依赖 V2Q-143）

按 V2Q-143（已确认方案甲，自动关联）实施：

1. 资源 `backend/resources/hanzi_pinyin.txt`：常用汉字（GB 2312 一、二级 6763 字）的全部无声调拼音读音，由 Unicode Unihan 数据库 `kMandarin` / `kHanyuPinyin` 字段生成（Unicode License，下载前取得同意；生成脚本放 `backend/scripts/`，不进镜像运行路径）。
2. 中文主体：3.1 第 1 步的“主体部分”；可去掉开头的地名后再比（英文名常省略地名）。
3. 英文主体：英文企业后缀之前、从首词起连续的“可完全切分为拼音音节”的单词，小写连接为 J（音节表取自资源文件的全部读音）；J 至少 2 个音节。
4. 若 J 等于中文主体部分某个前缀（≥ 2 个汉字，任一读音组合）的拼音连写，两者关联。**只做一对一关联**：一个英文主体只匹配到一个中文主体、反之亦然时才合并，否则都不合并。
5. 合并后共用中文主体的代号；英文写法作为别名（与 9.3 第 3 步简称相同的方式，对照表 `代号#n`）；国家、企业类型以中文主体为准；不写入已记住的名称。
6. 追问入口按本任务对照表做同样的关联。


### 12.5 英文常用词表（V2-B-14，依赖 V2Q-144）

1. 资源 `backend/resources/en_words.txt`：按 V2Q-144 确定的来源生成的常用英文词（小写，每行一个，≤ 1 MB），README 注明来源、版本与许可证；下载前把文件名、来源、大小告诉用户并取得同意。
2. 首字母大写段：段中每个单词都属于 common\_terms、月份星期、国家城市港口、文内小写佐证（12.3 第 3 条）或 `en_words.txt` 之一时不列；**至少有一个单词不在这些集合里**时才列（“Velmora Refinery”“Hardgrave Method”列，“Kinematic Viscosity”“Document Title”不列）。
3. 只作用于疑似遗漏的 capitalized 原因；企业后缀识别、人名、实体识别不受影响。

### 12.6 测试用例（T-53）

单个任务只跑：本节新测试文件 `test_v2b_feedback.py`、`test_redaction_golden.py`、`test_a03_outbound.py`、`test_suspects.py`、`test_redaction_engine.py`、`test_v2b_derived.py`、`test_ner.py`（若存在）、`test_chat.py`；V2-B-12 另跑前端 build。实体识别相关用例用 monkeypatch 固定 `ner.recognize` 返回值，不依赖模型分数。

| # | 任务 | 输入 | 期望 |
|-|-|-|-|
| 1 | 12 | 回归样本 real\_feedback\_bilingual | known\_gaps 三条移入 must\_replace（PERSON，文字不变）；追加 must\_keep：我方、现指定、Calcined、先生 |
| 2 | 12 | “我方澄川虚构碳材料股份有限公司，现指定青岚虚构国际贸易有限公司为代理” | 两个主体名称恰为“澄川虚构碳材料股份有限公司”“青岚虚构国际贸易有限公司”；“我方”“现指定”在外发文本中 |
| 3 | 12 | “现代虚构重工有限公司”“特变虚构电工有限公司” | 名称完整，不被截断 |
| 4 | 12 | 已记住名称“我方虚构科技有限公司”存在时的同句 | 仍按已记住名称整段替换（不迁移，行为不变） |
| 5 | 12 | “尊敬的柯墨兰先生：”“请欧阳明月女士确认” | “柯墨兰”“欧阳明月”替换为人员代号，“先生”“女士”保留 |
| 6 | 12 | “各位先生”“女士们”“叶先生” | 前两个无人名；“叶先生”按 3.2 整段替换（不变） |
| 7 | 12 | “DEAR MR.KE MOLAN:”“Dear Mrs Lorna Fictive,”“Attn: MISS JANE FICTIVE” | 只替换姓名部分 |
| 8 | 12 | “Dr Street 12”“Main Dr. ” 后无大写词 | 不识别人名 |
| 9 | 12 | “Product Name: Calcined Needle Coke”“Item Name: Graphite Electrode”“Contact Name: Lorna Fictive”“Company Name: Northwind Fictive” | 前两个无人名；后两个与现状相同（Contact Name 识别人名，Company Name 行为不变） |
| 10 | 12 | 同一文字在 5 个节点出现、原因分别为 capitalized 与 ner | `suspect_views` 返回一行，occurrences=5；ignore 后 5 条全部 ignore；replace 后全文替换 |
| 11 | 12 | 两组不同文字，各 3 处 | 第二步计数为 2；发送前检查按组数 |
| 12 | 12 | ner 返回“approved suppliers”“ASTM D4052”“GB/T 24526”“T/ZGTS 002-2022”“ASTM”“We”“The inspector” | 都不进入替换或疑似遗漏 |
| 13 | 12 | ner 返回“Velmora Refinery”“CCIC” | 仍按置信度分档进入替换或疑似遗漏（过滤不误伤） |
| 14 | 12 | “B. Feedstock Base”且正文另有“feedstock base” | 不列；“B. Velmora Base”列“Velmora Base” |
| 15 | 12 | 正文有“Kinematic Viscosity”但没有小写写法 | 本任务仍列（留给 12.5）；正文另有“kinematic viscosity”时不列 |
| 16 | 12 | 追问“Bulk Density 偏低怎么办，bulk density 指什么？” | 追问入口只用问题文本做小写佐证，不列 |
| 17 | 13 | “澄川虚构碳材料股份有限公司”与“Chengchuan Fictive Carbon Materials Co., Ltd.” | 同一代号；英文写法为别名；还原后与原文一致 |
| 18 | 13 | “青岚虚构国际贸易有限公司”与“Qingdao Qinglan Fictive International Trade Co., Ltd.”、“Qinglan Fictive Trade Co., Ltd.” | 前者带地名、后者无地名，均关联同一代号 |
| 19 | 13 | 两个中文主体字号拼音相同（“华信虚构”“华新虚构”）与一个英文“Huaxin Fictive Co., Ltd.” | 不合并（非一对一） |
| 20 | 13 | “Northwind Fictive International Limited”（非拼音） | 不关联任何中文主体 |
| 21 | 13 | 追问中出现英文写法 | 替换为已有的中文主体代号 |
| 22 | 14 | “Kinematic Viscosity”“Document Title”（无小写写法） | 不列 |
| 23 | 14 | “Velmora Refinery”“Hardgrave Machine Method” | 列 |
| 24 | 14 | 仿真实 PDF 的虚构样本 `en_spec_suspects.txt`（V2-B-14 开工前由 Opus 写入，≥ 200 行） | 疑似遗漏组数 ≤ 30，且 must\_suspect 全部列出 |
| 25 | 全部 | 现有回归集、A-03 | 全部通过；如有原样本因本节规则导致失败，停下报告，不改样本 |
