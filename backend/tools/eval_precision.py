"""脱敏精度本机评测：对一批文件跑解析与脱敏，明细只写到本机输出目录，标准输出只有计数。

用途：量“陌生文件里被误当成敏感词的有多少”。真实合同可以用，原文、名称、金额都不进标准输出与日志：
- 输出目录（不要放在仓库里）：
  - 标注表.xlsx：不确定项（默认替换）与确定项各一张表，每个词一行，带一处上下文，留一列人工判断；
  - N-外发文本.txt：每份文件脱敏后的正文，用来找漏掉的敏感信息。
- 标准输出：每份文件按序号给段数、字数、耗时与各来源、各原因的计数；不出现文件名和任何文字。

不连数据库、不联网、不调用大模型；不读已记住的名称（等同一个新用户第一次打开这份文件）。
DOC / WPS / RTF / XLS / ET 需要 converter 容器在运行并挂上同一个 /spool。

    python tools/eval_precision.py --out /out /in/1.docx /in/2.pdf
"""
import argparse
import collections
import sys
import time
from pathlib import Path

from openpyxl import Workbook

from app.parsing import docx, image, legacy, pdf, xlsx
from app.parsing.model import ParseError
from app.redaction import engine, ner
from app.redaction import parties as P

REASONS = {
    'org_suffix': '带企业后缀', 'before_company': '“公司 / 厂 / 机构”前的名称', 'capitalized': '英文首字母大写',
    'long_digits': '长串数字', 'person_name': '叙述人名', 'person_like': '叙述人名', 'ner': '实体识别（中置信）',
    'derived_name': '主体派生的字号', 'checksum_fail': '校验不过的号码',
}
SOURCES = {
    'party': '签约方', 'alias': '简称', 'term': '已记住的名称', 'user': '用户词条', 'derived': '派生字号', 'regex': '格式规则',
    'amount': '金额', 'person': '标签人名', 'address': '地址', 'date': '日期', 'ner': '实体识别（高置信）',
    'uncertain': '不确定项默认替换', 'recheck': '自检补替换',
}


def parse(path: Path, seq: int):
    data, ext = path.read_bytes(), path.suffix.lower().lstrip('.')
    if ext in legacy.LEGACY_EXTS:
        data, ext = legacy.convert(data, ext), legacy.result_ext(ext)
    if ext == 'docx':
        return docx.parse_docx(data, seq)
    if ext == 'xlsx':
        return xlsx.parse_xlsx(data, seq)
    if ext == 'pdf':
        return pdf.parse_pdf(data, seq)
    if ext in ('jpg', 'jpeg', 'png'):
        return image.parse_image(data, seq)
    raise ParseError('不支持的文件类型')


def redact(nodes):
    """与 engine._run 相同的两步：整体识别，再用向量辅助逐处保留。"""
    result = engine.compute(nodes, 'purchase')
    kept = set()
    try:
        from app.redaction.vector_assist import keep_spans
        kept = keep_spans(nodes, result.suspects, set())
    except Exception:  # noqa: BLE001  向量模型不可用时照常默认替换
        kept = set()
    if kept:
        result = engine.compute(nodes, 'purchase', ner_cache=result.ner_cache, kept_spans=kept)
    return result, len(kept)


def context(text: str, start: int, end: int, width: int = 18) -> str:
    return (text[max(0, start - width):start] + '【' + text[start:end] + '】' + text[end:end + width]).replace('\n', ' ')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('files', nargs='+')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ner.check(load=True)

    book = Workbook()
    uncertain_sheet = book.active
    uncertain_sheet.title = '不确定项'
    uncertain_sheet.append(['文件', '文件名', '被替换的词', '为什么被列出', '代号', '次数', '上下文（一处）', '你的判断（该换 / 不该换）'])
    sure_sheet = book.create_sheet('确定项')
    sure_sheet.append(['文件', '文件名', '被替换的词', '来源', '代号', '次数', '上下文（一处）', '替换错了的在这里写“错”'])
    total = collections.Counter()

    for seq, name in enumerate(args.files, start=1):
        path = Path(name)
        started = time.perf_counter()
        try:
            parsed = parse(path, seq)
        except ParseError as exc:
            print(f'文件 {seq}：解析失败（{exc}）', flush=True)
            continue
        parsed_s = time.perf_counter() - started
        nodes = [engine.NodeIn(n.loc, n.text, n.clause_no, n.kind) for n in parsed.nodes if n.part == 'body' and n.text.strip()]
        started = time.perf_counter()
        result, vector_kept = redact(nodes)
        redact_s = time.perf_counter() - started
        texts = {n.loc: n.text for n in nodes}
        (out / f'{seq}-外发文本.txt').write_text(
            '\n'.join(f'[{n.loc}] {result.redacted[n.loc]}' for n in nodes), encoding='utf-8')

        reason_of = {}
        for s in result.suspects:
            reason_of.setdefault(P.normalize(texts[s.loc][s.char_start:s.char_end]), s.reason)
        rows: dict[tuple, list] = {}
        for item in result.items:
            surface = texts[item.loc][item.char_start:item.char_end]
            key = (item.detected_by == 'uncertain', P.normalize(surface), item.placeholder)
            if key not in rows:
                why = (REASONS.get(reason_of.get(key[1], ''), '其他') if key[0]
                       else SOURCES.get(item.detected_by, item.detected_by))
                if item.detected_by == 'recheck':
                    why += f'（{item.category}）'
                rows[key] = [seq, path.name, surface, why, item.placeholder, 0,
                             context(texts[item.loc], item.char_start, item.char_end), '']
            rows[key][5] += 1
        for key, row in rows.items():
            (uncertain_sheet if key[0] else sure_sheet).append(row)

        by_source = collections.Counter(SOURCES.get(i.detected_by, i.detected_by) for i in result.items)
        by_reason = collections.Counter(row[3] for key, row in rows.items() if key[0])
        # V321Q-10：只输出类别与处数，不把补替换的原文带到终端。
        by_recheck_category = collections.Counter(i.category for i in result.items if i.detected_by == 'recheck')
        unique_uncertain = sum(1 for key in rows if key[0])
        total.update({'文件': 1, '段': len(nodes), '字': sum(len(n.text) for n in nodes),
                      '不确定项（去重）': unique_uncertain, '确定项（去重）': len(rows) - unique_uncertain})
        total.update({f'自检补替换/{category}': count for category, count in by_recheck_category.items()})
        print(f'文件 {seq}：段 {len(nodes)}，字 {sum(len(n.text) for n in nodes)}，OCR 页 {parsed.ocr_pages}，'
              f'解析 {parsed_s:.1f}s，脱敏 {redact_s:.1f}s；替换处数按来源 {dict(by_source)}；'
              f'不确定项去重 {unique_uncertain} 个，按原因 {dict(by_reason)}；'
              f'自检补替换按类别 {dict(by_recheck_category)}；向量辅助保留 {vector_kept} 处', flush=True)

    for sheet in (uncertain_sheet, sure_sheet):
        for column, width in zip('ABCDEFGH', (6, 28, 30, 26, 16, 6, 60, 26)):
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = 'A2'
    book.save(out / '标注表.xlsx')
    print('合计：', dict(total), file=sys.stdout, flush=True)


if __name__ == '__main__':
    main()
