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

from openpyxl import Workbook, load_workbook
import yaml

from app.parsing import docx, image, legacy, pdf, xlsx
from app.parsing.model import ParseError
from app.redaction import detect, engine, ner
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
    """与 engine._run 相同，不再用向量辅助保留不确定项。"""
    return engine.compute(nodes, 'purchase')


def context(text: str, start: int, end: int, width: int = 18) -> str:
    return (text[max(0, start - width):start] + '【' + text[start:end] + '】' + text[end:end + width]).replace('\n', ' ')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--labels', help='上次评测的人工标注表')
    ap.add_argument('--golden', nargs='+', metavar='样本名', help='计算虚构 golden 的每千字误替换数')
    ap.add_argument('files', nargs='*')
    args = ap.parse_args()
    if args.golden:
        if args.files or args.labels:
            ap.error('--golden 不能与真实文件或 --labels 同用')
        root = Path(__file__).resolve().parents[1] / 'tests/fixtures/redaction_golden'
        ner.check(load=True)
        length = errors = 0
        for name in args.golden:
            if Path(name).name != name:
                ap.error('样本名不能包含路径')
            lines = (root / f'{name}.txt').read_text(encoding='utf-8').splitlines()
            ann = yaml.safe_load((root / f'{name}.yaml').read_text(encoding='utf-8'))
            nodes = [engine.NodeIn(f'F1-P{i}', line) for i, line in enumerate(lines, 1) if line.strip()]
            result = redact(nodes)
            length += sum(len(n.text) for n in nodes)
            for node in nodes:
                for keep in ann['must_keep']:
                    start = node.text.find(keep)
                    while start >= 0:
                        end = start + len(keep)
                        errors += int(any(i.loc == node.loc and i.char_start < end and i.char_end > start
                                          for i in result.items))
                        start = node.text.find(keep, start + 1)
        print(f'虚构样本：{length} 字，误替换 {errors} 处，每千字 {errors * 1000 / length:.2f}' if length else '虚构样本为空')
        return
    if not args.out or not args.files:
        ap.error('文件评测需要 --out 和输入文件')
    out = Path(args.out)
    if out.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        ap.error('输出目录必须在仓库外')
    out.mkdir(parents=True, exist_ok=True)
    ner.check(load=True)

    labels = []
    if args.labels:
        old = load_workbook(args.labels, read_only=True, data_only=True)
        label_files = {}
        for sheet, kind in ((old['不确定项'], 'uncertain'), (old['确定项'], 'sure')):
            for row in sheet.iter_rows(min_row=2, values_only=True):
                seq, filename, word, _, _, _, _, judgment = row[:8]
                if not isinstance(seq, int) or not word:
                    continue
                if seq in label_files and label_files[seq] != filename:
                    raise SystemExit('标注表中的文件序号不一致')
                label_files[seq] = filename
                judgment = str(judgment or '').strip()
                group = ('keep' if judgment == '不该换' else 'replace' if judgment == '该换'
                         else 'sure' if kind == 'sure' and not judgment else None)
                labels.append((seq, P.normalize(str(word)), group))
        old.close()
        if set(label_files) != set(range(1, len(args.files) + 1)) or any(
                Path(name).name != label_files[seq] for seq, name in enumerate(args.files, 1)):
            raise SystemExit('输入文件与标注表的序号或文件名不一致')
    baseline_keys = {(seq, word) for seq, word, _ in labels}
    matches = set()
    new_keys = set()

    book = Workbook()
    uncertain_sheet = book.active
    uncertain_sheet.title = '不确定项'
    uncertain_sheet.append(['文件', '文件名', '被替换的词', '为什么被列出', '代号', '次数', '上下文（一处）', '你的判断（该换 / 不该换）', '规则 / 类别'])
    sure_sheet = book.create_sheet('确定项')
    sure_sheet.append(['文件', '文件名', '被替换的词', '来源', '代号', '次数', '上下文（一处）', '替换错了的在这里写“错”', '规则 / 类别'])
    new_sheet = book.create_sheet('新出现') if args.labels else None
    if new_sheet:
        new_sheet.append(['文件', '文件名', '被替换的词', '来源', '代号', '次数', '上下文（一处）', '你的判断', '规则 / 类别'])
    total = collections.Counter()

    for seq, name in enumerate(args.files, start=1):
        path = Path(name)
        started = time.perf_counter()
        try:
            parsed = parse(path, seq)
        except ParseError:
            raise SystemExit(f'文件 {seq} 解析失败，评测未完成') from None
        parsed_s = time.perf_counter() - started
        nodes = [engine.NodeIn(n.loc, n.text, n.clause_no, n.kind) for n in parsed.nodes if n.part == 'body' and n.text.strip()]
        started = time.perf_counter()
        result = redact(nodes)
        redact_s = time.perf_counter() - started
        texts = {n.loc: n.text for n in nodes}
        # 人名会全文传播；引擎不保存最初的命中规则，只列能在本文件复现的候选入口。
        person_rules = collections.defaultdict(set)
        for node in nodes:
            for hit in detect.find_persons(node.text):
                person_rules[P.normalize(node.text[hit.start:hit.end])].add('find_persons')
        for index, start, end in detect.find_signature_persons([node.text for node in nodes]):
            person_rules[P.normalize(nodes[index].text[start:end])].add('find_signature_persons')
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
                rule = item.detected_by
                if item.detected_by == 'uncertain':
                    rule += '/' + reason_of.get(key[1], 'unknown')
                if item.detected_by == 'person' and person_rules.get(key[1]):
                    rule += '/候选:' + ','.join(sorted(person_rules[key[1]]))
                rule += '/' + item.category
                rows[key] = [seq, path.name, surface, why, item.placeholder, 0,
                             context(texts[item.loc], item.char_start, item.char_end), '', rule]
            rows[key][5] += 1
            if args.labels:
                normalized = P.normalize(surface)
                if (seq, normalized) not in baseline_keys:
                    new_keys.add((seq, normalized))
                for label_seq, word, _ in labels:
                    if label_seq == seq and word and word in normalized:
                        matches.add((seq, word))
        for key, row in rows.items():
            (uncertain_sheet if key[0] else sure_sheet).append(row)
            if new_sheet and (seq, key[1]) in new_keys:
                new_sheet.append(row)

        by_source = collections.Counter(SOURCES.get(i.detected_by, i.detected_by) for i in result.items)
        by_reason = collections.Counter(row[3] for key, row in rows.items() if key[0])
        # V321Q-10：只输出类别与处数，不把补替换的原文带到终端。
        by_recheck_category = collections.Counter(i.category for i in result.items if i.detected_by == 'recheck')
        unique_uncertain = sum(1 for key in rows if key[0])
        total.update({'文件': 1, '段': len(nodes), '字': sum(len(n.text) for n in nodes),
                      '不确定项（去重）': unique_uncertain, '确定项（去重）': len(rows) - unique_uncertain})
        total.update({f'自检补替换/{category}': count for category, count in by_recheck_category.items()})
        if not args.labels:
            print(f'文件 {seq}：段 {len(nodes)}，字 {sum(len(n.text) for n in nodes)}，OCR 页 {parsed.ocr_pages}，'
              f'解析 {parsed_s:.1f}s，脱敏 {redact_s:.1f}s；替换处数按来源 {dict(by_source)}；'
              f'不确定项去重 {unique_uncertain} 个，按原因 {dict(by_reason)}；'
              f'自检补替换按类别 {dict(by_recheck_category)}', flush=True)

    for sheet in book.worksheets:
        for column, width in zip('ABCDEFGHI', (6, 28, 30, 26, 16, 6, 60, 26, 28)):
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = 'A2'
    book.save(out / '标注表.xlsx')
    if args.labels:
        for group, title in (('keep', '不该换仍被替换'), ('replace', '该换仍被替换'), ('sure', '确定项仍被替换')):
            entries = [(seq, word) for seq, word, g in labels if g == group]
            print(f'{title} {sum(key in matches for key in entries)}/{len(entries)}')
        print(f'新出现的替换词 {len(new_keys)} 个')
    else:
        print('合计：', dict(total), file=sys.stdout, flush=True)


if __name__ == '__main__':
    main()
