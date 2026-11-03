"""V31-M-01 评分：字错率、发言人归属错误、低置信阈值（design/V31-M.md 1.3、1.4）。纯标准库。

  python m01_score.py <脚本 md> <段号 1-3> <m01_eval 输出 json> [--fp32 <m01_fp32_ref 输出 json>]

参照文本取录音脚本该段的台词（去掉“甲：”与标点），英文大写比较。
"""
import argparse
import json
import re
import unicodedata

SPK = {"甲": 1, "乙": 2, "丙": 3, "丁": 4}
SECTIONS = ["第一段", "第二段", "第三段"]


def norm_chars(text):
    out = []
    for c in unicodedata.normalize("NFKC", text):
        if c.isalnum():
            out.append(c.upper())
    return out


def load_reference(md_path, part):
    text = open(md_path, encoding="utf-8").read()
    start = text.index("## " + SECTIONS[part - 1])
    end = text.find("\n## ", start + 3)
    chars, spk = [], []
    for m in re.finditer(r"^\*\*(甲|乙|丙|丁)\*\*：(.+)$", text[start:end], re.M):
        cs = norm_chars(m.group(2))
        chars += cs
        spk += [SPK[m.group(1)]] * len(cs)
    return chars, spk


def align(ref, hyp):
    """编辑距离与回溯：返回 (距离, hyp 每个字对应的 ref 下标或 None, hyp 每个字是否错误)。"""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    back = [bytearray(m + 1) for _ in range(n + 1)]  # 0 对角 1 删 ref 2 插 hyp
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        bi = back[i]
        bi[0] = 1
        r = ref[i - 1]
        for j in range(1, m + 1):
            d = prev[j - 1] + (r != hyp[j - 1])
            u = prev[j] + 1
            l = cur[j - 1] + 1
            if d <= u and d <= l:
                cur[j] = d
            elif u <= l:
                cur[j], bi[j] = u, 1
            else:
                cur[j], bi[j] = l, 2
        prev = cur
    dist = prev[m]
    to_ref, wrong = [None] * m, [True] * m
    i, j = n, m
    while i > 0 or j > 0:
        b = back[i][j]
        if b == 0:
            to_ref[j - 1] = i - 1
            wrong[j - 1] = ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif b == 1:
            i -= 1
        else:
            j -= 1
    return dist, to_ref, wrong


def hyp_chars(segments):
    chars, seg_of, conf = [], [], []
    for k, s in enumerate(segments):
        raw = s.get("raw", s["text"])
        cs = s.get("conf")
        for idx, c in enumerate(unicodedata.normalize("NFKC", raw)):
            if c.isalnum():
                chars.append(c.upper())
                seg_of.append(k)
                conf.append(cs[idx] if cs and idx < len(cs) else None)
    return chars, seg_of, conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("part", type=int)
    ap.add_argument("result")
    ap.add_argument("--fp32")
    a = ap.parse_args()

    ref, ref_spk = load_reference(a.script, a.part)
    res = json.load(open(a.result, encoding="utf-8"))
    hyp, seg_of, conf = hyp_chars(res["segments"])
    dist, to_ref, wrong = align(ref, hyp)
    report = {"ref_chars": len(ref), "hyp_chars": len(hyp), "cer": round(dist / len(ref), 4)}

    # 发言人：每段取对齐字的多数参照发言人；聚类号到参照发言人按字数贪心对应
    seg_ref = {}
    for j, i in enumerate(to_ref):
        if i is not None:
            seg_ref.setdefault(seg_of[j], []).append(ref_spk[i])
    votes = {}
    seg_major = {}
    for k, lst in seg_ref.items():
        major = max(set(lst), key=lst.count)
        seg_major[k] = major
        spk = res["segments"][k]["speaker"]
        votes[(spk, major)] = votes.get((spk, major), 0) + len(lst)
    mapping, used = {}, set()
    for (spk, major), _ in sorted(votes.items(), key=lambda kv: -kv[1]):
        if spk not in mapping and major not in used:
            mapping[spk] = major
            used.add(major)
    bad = sum(1 for k, mj in seg_major.items() if mapping.get(res["segments"][k]["speaker"]) != mj)
    report["speaker_seg_error"] = round(bad / max(1, len(seg_major)), 4)
    report["speakers_found"] = len({s["speaker"] for s in res["segments"]})

    # 低置信：取使约 70% 错字落在高亮内的阈值，报高亮占全部字的比例
    pairs = [(c, w) for c, w in zip(conf, wrong) if c is not None]
    if pairs:
        err = sorted(c for c, w in pairs if w)
        if err:
            th = err[min(len(err) - 1, int(len(err) * 0.7))]
            report["low_conf_threshold"] = round(th, 4)
            report["low_conf_marked_ratio"] = round(sum(1 for c, _ in pairs if c <= th) / len(pairs), 4)
            report["low_conf_error_recall"] = round(sum(1 for c in err if c <= th) / len(err), 4)

    if a.fp32:
        fp = json.load(open(a.fp32, encoding="utf-8"))
        fh, _, _ = hyp_chars(fp["segments"])
        report["cer_fp32"] = round(align(ref, fh)[0] / len(ref), 4)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
