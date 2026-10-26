"""V2-E-04a：把官方全文 docx 按 design/V2-E.md 5.4 的拆条规则拆成条文（评测用，不是产品代码）。

在 aireview-backend-test 镜像中断网运行，复用产品的 parse_docx：
  python split_laws.py /laws /out/law_articles.jsonl
输入目录中的文件名为 `NN_{简称}_{YYYYMMDD}.docx`；完整标题写在 TITLES 中。
"""
import json
import re
import sys
from pathlib import Path

from app.parsing.docx import parse_docx

TITLES = {
    "民法典": ("中华人民共和国民法典", "法律"),
    "买卖合同解释": ("最高人民法院关于审理买卖合同纠纷案件适用法律问题的解释", "司法解释"),
    "公司法": ("中华人民共和国公司法", "法律"),
    "劳动合同法": ("中华人民共和国劳动合同法", "法律"),
    "仲裁法": ("中华人民共和国仲裁法", "法律"),
    "民事诉讼法": ("中华人民共和国民事诉讼法", "法律"),
    "电子商务法": ("中华人民共和国电子商务法", "法律"),
    "对外贸易法": ("中华人民共和国对外贸易法", "法律"),
    "合同编通则解释": ("最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释", "司法解释"),
}

CN = "零〇一二三四五六七八九十百千"
ARTICLE = re.compile(rf"^(第[{CN}]+条(?:之[一二三四五六七八九十]+)?)[　 \t]+")
HEADING = re.compile(rf"^第[{CN}]+(编|分编|章|节)[　 ]")
PART = re.compile(r"^[一二三四五六七八九十]+、")


def is_heading(text: str) -> bool:
    bare = re.sub(r"\s", "", text)
    if HEADING.match(text) or bare in ("总则", "分则", "附则"):
        return True
    return bool(PART.match(text)) and len(text) <= 30 and "。" not in text


def split(nodes) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        if n.part != "body":
            continue
        text = n.text.lstrip()
        if not text:
            continue
        m = ARTICLE.match(text)
        if m:
            out.append({"article_no": m.group(1), "text": text[m.end():].strip()})
        elif is_heading(text):
            continue
        elif out:
            out[-1]["text"] += "\n" + text.strip()
    return out


def main(src: str, dst: str) -> None:
    rows = []
    for path in sorted(Path(src).glob("*.docx")):
        _, short, date = path.stem.split("_")
        title, category = TITLES[short]
        arts = split(parse_docx(path.read_bytes(), 1).nodes)
        for seq, a in enumerate(arts, 1):
            rows.append({"law": short, "title": title, "category": category,
                         "published_on": f"{date[:4]}-{date[4:6]}-{date[6:]}", "seq": seq, **a})
        print(short, len(arts))
    with open(dst, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("total", len(rows))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
