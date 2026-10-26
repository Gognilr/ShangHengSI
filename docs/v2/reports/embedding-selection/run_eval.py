"""V2-E-04a 向量模型选型评测（评测用，不是产品代码）。

只用 onnxruntime + tokenizers + numpy（与 design/V2-E.md 5.2 的引擎约束一致），在断网容器中运行：
  python run_eval.py accuracy <variant> --threads 8          # 准确率与阈值，结果写 /out/<variant>.json
  python run_eval.py perf <variant> --threads 8 --chars 10000 # 1 万字耗时与内存（worker 场景）
  python run_eval.py appmem <variant> --threads 4             # app 首次加载并计算一次追问向量的内存
  python run_eval.py reference <variant>                      # 参考向量（V2-E-08 对拍用）
模型目录挂载在 /models，评测集挂载在 /fixtures（backend/tests/fixtures/embedding），输出在 /out。
"""
import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path

MODELS = Path("/models")
FIX = Path("/fixtures")
OUT = Path("/out")

VARIANTS = {
    # name: (onnx 文件, tokenizer.json, 池化方式, query 前缀, passage 前缀)
    "bge-small-zh-fp32": ("bge-small-zh/onnx/model.onnx", "bge-small-zh/tokenizer.json", "cls", "为这个句子生成表示以用于检索相关文章：", ""),
    "bge-small-zh-int8": ("bge-small-zh/onnx/model_int8.onnx", "bge-small-zh/tokenizer.json", "cls", "为这个句子生成表示以用于检索相关文章：", ""),
    "e5-small-fp32": ("e5-small/onnx/model.onnx", "e5-small/onnx/tokenizer.json", "mean", "query: ", "passage: "),
    "e5-small-int8": ("e5-small/onnx/model_int8.onnx", "e5-small/onnx/tokenizer.json", "mean", "query: ", "passage: "),
    "bge-m3-fp32": ("bge-m3/onnx/model.onnx", "bge-m3/onnx/tokenizer.json", "cls", "", ""),
    "bge-m3-int8": ("bge-m3/onnx/model_int8.onnx", "bge-m3/onnx/tokenizer.json", "cls", "", ""),
}
MAX_TOKENS = 512
BATCH = int(os.environ.get("EMB_BATCH", "16"))  # 设计 5.2：每批 ≤ 16 段
ARENA = os.environ.get("EMB_ARENA", "1") == "1"  # 0 = 关闭 onnxruntime 的 CPU 内存池
OPT = os.environ.get("EMB_OPT", "all")  # 图优化级别：all / extended / basic / none


def rss_kb(field: str = "VmRSS") -> int:
    for line in open("/proc/self/status"):
        if line.startswith(field + ":"):
            return int(line.split()[1])
    return 0


class Engine:
    def __init__(self, variant: str, threads: int):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx, tok, self.pooling, self.qp, self.pp = VARIANTS[variant]
        self.tok = Tokenizer.from_file(str(MODELS / tok))
        self.tok.enable_truncation(MAX_TOKENS)
        pad_id = self.tok.token_to_id("[PAD]")
        if pad_id is None:
            pad_id = self.tok.token_to_id("<pad>")
        self.tok.enable_padding(pad_id=pad_id)
        ort.disable_telemetry_events() if hasattr(ort, "disable_telemetry_events") else None
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.enable_cpu_mem_arena = ARENA
        so.enable_mem_pattern = ARENA
        so.graph_optimization_level = {"all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
                                       "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
                                       "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
                                       "none": ort.GraphOptimizationLevel.ORT_DISABLE_ALL}[OPT]
        self.sess = ort.InferenceSession(str(MODELS / onnx), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.out_name = self.sess.get_outputs()[0].name

    def embed(self, texts, kind):
        import numpy as np

        prefix = self.qp if kind == "query" else self.pp
        out = []
        for i in range(0, len(texts), BATCH):
            encs = self.tok.encode_batch([prefix + t for t in texts[i:i + BATCH]])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run([self.out_name], feed)[0]
            if self.pooling == "cls":
                vec = hidden[:, 0, :]
            else:
                m = mask[:, :, None].astype(np.float32)
                vec = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            vec = vec / np.linalg.norm(vec, axis=1, keepdims=True)
            out.append(vec.astype(np.float32))
        return np.concatenate(out)


CN_DIGITS = "零一二三四五六七八九"


def cn_number(n: int) -> str:
    # 1～9999 的中文数字，规则与官方法条编号一致（如 一百零一、一千零一、二十）
    units = ["", "十", "百", "千"]
    s = ""
    digits = [int(d) for d in str(n)]
    zero = False
    for i, d in enumerate(digits):
        pos = len(digits) - 1 - i
        if d == 0:
            zero = True
            continue
        if zero and s:
            s += "零"
        zero = False
        s += CN_DIGITS[d] + units[pos]
    if s.startswith("一十"):
        s = s[1:]
    return s


def gold_key(ref: str) -> tuple[str, str]:
    law, no = ref.split(":")
    return law, f"第{cn_number(int(no))}条"


def floor2(x: float) -> float:
    return math.floor(x * 100) / 100


def ceil2(x: float) -> float:
    return math.ceil(x * 100 + 1e-9) / 100


def clause_text(c: dict) -> str:
    return f"{c['title']}\n{c['text']}"


def accuracy(variant: str, threads: int) -> None:
    import numpy as np

    eng = Engine(variant, threads)
    result = {"variant": variant}

    # ---------- 法条检索 ----------
    arts = [json.loads(l) for l in open(FIX / "law_articles.jsonl", encoding="utf-8")]
    keys = [(a["law"], a["article_no"]) for a in arts]
    t0 = time.perf_counter()
    A = eng.embed([f"《{a['title']}》{a['article_no']}：{a['text']}" for a in arts], "passage")
    result["law_corpus_seconds"] = round(time.perf_counter() - t0, 1)
    lr = json.load(open(FIX / "law_retrieval.json", encoding="utf-8"))
    for g in (g for it in lr["items"] for g in it["gold"]):
        assert gold_key(g) in keys, f"gold 不在语料中：{g}"
    law_res = {}
    for mode in ("passage", "query"):
        Q = eng.embed([it["text"] for it in lr["items"]], mode)
        S = Q @ A.T
        per = []
        for it, row in zip(lr["items"], S):
            order = np.argsort(-row)
            golds = {gold_key(g) for g in it["gold"]}
            ranks = [r for r, j in enumerate(order[:50]) if keys[j] in golds]
            best_gold = max(float(row[keys.index(g)]) for g in golds)
            per.append({"id": it["id"], "rank": (ranks[0] + 1) if ranks else None, "best_gold": round(best_gold, 4),
                        "top1": round(float(row[order[0]]), 4), "top10": round(float(row[order[9]]), 4),
                        "en": not any("一" <= ch <= "鿿" for ch in it["text"])})
        hit10 = [p for p in per if p["rank"] and p["rank"] <= 10]
        hit5 = [p for p in per if p["rank"] and p["rank"] <= 5]
        mrr = sum(1 / p["rank"] for p in per if p["rank"]) / len(per)
        en = [p for p in per if p["en"]]
        law_res[mode] = {"recall10": len(hit10) / len(per), "recall5": len(hit5) / len(per), "mrr": round(mrr, 4),
                         "recall10_en": sum(1 for p in en if p["rank"] and p["rank"] <= 10) / len(en),
                         "misses": [p["id"] for p in per if not (p["rank"] and p["rank"] <= 10)], "per_item": per}
        # LAW_MIN_SCORE：不损失前 10 名命中的最高阈值（取命中条目中最佳正确法条分数的最小值，向下取两位）
        thr = floor2(min(p["best_gold"] for p in hit10)) if hit10 else None
        O = eng.embed([o["text"] for o in lr["offtopic"]], mode)
        OS = O @ A.T
        off_top5 = np.sort(OS, axis=1)[:, ::-1][:, :5]
        law_res[mode]["law_min_score"] = thr
        law_res[mode]["offtopic_top1"] = [round(float(x), 4) for x in off_top5[:, 0]]
        law_res[mode]["offtopic_filtered_all"] = int((off_top5[:, 0] < thr).sum()) if thr else None
        law_res[mode]["offtopic_kept_of_50"] = int((off_top5 >= thr).sum()) if thr else None
        # 相关查询在该阈值下前 5 名平均保留几条
        top5 = np.sort(S, axis=1)[:, ::-1][:, :5]
        law_res[mode]["relevant_kept_of_500"] = int((top5 >= thr).sum()) if thr else None
    result["law"] = law_res

    # ---------- 范本对照 ----------
    tp = json.load(open(FIX / "template_pairs.json", encoding="utf-8"))
    T = eng.embed([clause_text(c) for c in tp["template"]], "passage")
    pair = {}
    unrelated = eng.embed([clause_text(c) for c in tp["unrelated"]], "passage")
    un_max = (unrelated @ T.T).max(1)
    thr = ceil2(float(un_max.max()) + 0.005)  # 严格高于全部无对应条款的最高分
    pair["unrelated_max"] = [round(float(x), 4) for x in un_max]
    pair["clause_pair_min"] = thr
    for lang in ("zh", "en"):
        C = eng.embed([clause_text(c[lang]) for c in tp["template"]], "passage")
        S = C @ T.T
        arg = S.argmax(1)
        correct = [i for i in range(len(arg)) if arg[i] == i]
        diag = S.diagonal()
        recall = [i for i in correct if diag[i] >= thr]
        # L-12：去掉第 i 条对应条款后，范本第 i 条与其余条款的最高分仍 ≥ 阈值即漏报
        missed_l12 = [tp["template"][i]["id"] for i in range(len(T)) if np.delete(S[:, i], i).max() >= thr]
        pair[lang] = {"best_match_acc": len(correct) / len(arg), "recall_at_thr": len(recall) / len(arg),
                      "wrong": [tp["template"][i]["id"] for i in range(len(arg)) if arg[i] != i],
                      "below_thr": [tp["template"][i]["id"] for i in correct if diag[i] < thr],
                      "diag_min": round(float(diag.min()), 4), "diag_mean": round(float(diag.mean()), 4),
                      "l12_missed": missed_l12}
    pair["best_match_acc_all"] = (pair["zh"]["best_match_acc"] + pair["en"]["best_match_acc"]) / 2
    result["pair"] = pair

    # ---------- 范本选择 ----------
    ts = json.load(open(FIX / "template_selection.json", encoding="utf-8"))
    by_id = {c["id"]: c for c in tp["template"]}
    tvecs = {}
    for tid, t in ts["templates"].items():
        cl = tp["template"] if t.get("from_pairs") else t["clauses"]
        tvecs[tid] = eng.embed([clause_text(c) for c in cl], "passage")

    def contract_clauses(c):
        cl = []
        if "pairs" in c:
            cl += [by_id[i][c["pairs"]["version"]] for i in c["pairs"]["ids"]]
        cl += c.get("extra", []) + c.get("clauses", [])
        return cl

    rows = []
    for c in ts["contracts"] + ts["negatives"]:
        V = eng.embed([clause_text(x) for x in contract_clauses(c)], "passage")
        avgs = {tid: float((V @ tv.T).max(1).mean()) for tid, tv in tvecs.items()}
        best = max(avgs, key=avgs.get)
        rows.append({"id": c["id"], "expect": c["expect"], "best": best, "avgs": {k: round(v, 4) for k, v in avgs.items()}})
    neg_best = max(max(r["avgs"].values()) for r in rows if r["expect"] is None)
    sel_thr = ceil2(neg_best + 0.005)
    pos = [r for r in rows if r["expect"]]
    result["select"] = {"template_select_min": sel_thr, "rows": rows,
                        "argmax_acc": sum(r["best"] == r["expect"] for r in pos) / len(pos),
                        "correct_at_thr": sum(r["best"] == r["expect"] and r["avgs"][r["best"]] >= sel_thr for r in pos) / len(pos),
                        "margin_min": round(min(r["avgs"][r["expect"]] - max(v for k, v in r["avgs"].items() if k != r["expect"]) for r in pos), 4)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{variant}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    np.save(OUT / f"{variant}-law.npy", A)
    summary = {k: result["law"]["passage"][k] for k in ("recall10", "recall5", "mrr", "recall10_en", "law_min_score", "offtopic_filtered_all", "misses")}
    print(variant, "LAW(passage)", summary)
    print(variant, "LAW(query)", {k: result["law"]["query"][k] for k in ("recall10", "recall5", "mrr", "recall10_en", "law_min_score")})
    print(variant, "PAIR", {k: pair[k] for k in ("clause_pair_min", "best_match_acc_all")}, {l: {k: pair[l][k] for k in ("best_match_acc", "recall_at_thr", "wrong", "below_thr", "l12_missed")} for l in ("zh", "en")})
    print(variant, "SELECT", {k: result["select"][k] for k in ("template_select_min", "argmax_acc", "correct_at_thr", "margin_min")})


def pairdetail(variant: str, threads: int) -> None:
    # L-12 分析：范本第 i 条的对应条款存在时的分数（diag）与“对应条款缺失、其余条款都在”时的最高分（loo）
    import numpy as np

    eng = Engine(variant, threads)
    tp = json.load(open(FIX / "template_pairs.json", encoding="utf-8"))
    T = eng.embed([clause_text(c) for c in tp["template"]], "passage")
    out = {}
    for lang in ("zh", "en"):
        C = eng.embed([clause_text(c[lang]) for c in tp["template"]], "passage")
        S = C @ T.T
        rows = []
        for i, c in enumerate(tp["template"]):
            col = S[:, i]
            loo = np.delete(col, i)
            j = int(np.argmax(loo))
            j = j if j < i else j + 1
            rows.append({"id": c["id"], "title": c["title"], "diag": round(float(col[i]), 4),
                         "loo_max": round(float(loo.max()), 4), "loo_by": tp["template"][j]["id"]})
        out[lang] = rows
    (OUT / f"pairdetail-{variant}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for lang, rows in out.items():
        dmin = min(r["diag"] for r in rows)
        for t in (0.70, 0.72, 0.74, 0.76, 0.78, 0.80):
            fp = sum(r["diag"] < t for r in rows)  # 对应条款存在却报“未找到”
            det = sum(r["loo_max"] < t for r in rows)  # 对应条款缺失时能报出
            print(variant, lang, f"t={t:.2f} 误报={fp} 检出={det}/30", f"diag_min={dmin}")


def perf_texts(chars: int) -> list[str]:
    # 1 万字：取范本条款与法条正文顺序拼接，切成 500 字一段（接近一级条款长度）
    tp = json.load(open(FIX / "template_pairs.json", encoding="utf-8"))
    arts = [json.loads(l) for l in open(FIX / "law_articles.jsonl", encoding="utf-8")]
    buf = "".join(c["text"] for c in tp["template"]) + "".join(a["text"] for a in arts[460:700])
    buf = buf[:chars]
    return [buf[i:i + 500] for i in range(0, len(buf), 500)]


def perf(variant: str, threads: int, chars: int) -> None:
    import numpy  # noqa: F401  导入运行库后再取基线，模型加载与推理的增量单列
    import onnxruntime  # noqa: F401
    import tokenizers  # noqa: F401

    base = rss_kb()
    t0 = time.perf_counter()
    eng = Engine(variant, threads)
    load_s = time.perf_counter() - t0
    after_load = {"rss": rss_kb(), "hwm": rss_kb("VmHWM")}
    texts = perf_texts(chars)
    eng.embed(texts[:2], "passage")  # 预热
    times = []
    for _ in range(3):
        t = time.perf_counter()
        eng.embed(texts, "passage")
        times.append(time.perf_counter() - t)
    hwm = rss_kb("VmHWM")
    r = {"variant": variant, "batch": BATCH, "arena": ARENA, "opt": OPT, "threads": threads, "chars": sum(map(len, texts)), "passages": len(texts),
         "load_seconds": round(load_s, 2), "embed_seconds": [round(x, 3) for x in times],
         "embed_median": round(statistics.median(times), 3), "rss_base_mb": round(base / 1024, 1),
         "peak_increase_gb": round((hwm - base) * 1024 / 1e9, 3), "cpu_count": os.cpu_count(),
         "load_peak_increase_gb": round((after_load["hwm"] - base) * 1024 / 1e9, 3),
         "after_load_rss_increase_gb": round((after_load["rss"] - base) * 1024 / 1e9, 3),
         "after_embed_rss_increase_gb": round((rss_kb() - base) * 1024 / 1e9, 3)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"perf-{variant}-t{threads}-b{BATCH}-a{int(ARENA)}-{OPT}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False))


def appmem(variant: str, threads: int) -> None:
    import numpy  # noqa: F401
    import onnxruntime  # noqa: F401
    import tokenizers  # noqa: F401

    base = rss_kb()
    t0 = time.perf_counter()
    eng = Engine(variant, threads)
    q = "这份采购合同约定买方在验收合格后三十日内付款，并保留百分之五的质保金，这样的付款条件对我方是否有风险？" * 2
    eng.embed([q], "query")
    first = time.perf_counter() - t0
    t = time.perf_counter()
    eng.embed([q], "query")
    again = time.perf_counter() - t
    hwm = rss_kb("VmHWM")
    r = {"variant": variant, "batch": BATCH, "arena": ARENA, "opt": OPT, "threads": threads, "first_load_and_query_seconds": round(first, 2),
         "query_seconds": round(again, 3), "peak_increase_gb": round((hwm - base) * 1024 / 1e9, 3)}
    (OUT / f"appmem-{variant}-t{threads}-b{BATCH}-a{int(ARENA)}-{OPT}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False))


def reference(variant: str) -> None:
    # 参考向量：固定 24 段输入（法条、范本条款中英文、无对应条款、追问），记录前缀方式与向量
    tp = json.load(open(FIX / "template_pairs.json", encoding="utf-8"))
    arts = [json.loads(l) for l in open(FIX / "law_articles.jsonl", encoding="utf-8")]
    pick = [a for a in arts if (a["law"], a["article_no"]) in {("民法典", "第五百八十五条"), ("民法典", "第五百九十条"), ("买卖合同解释", "第十五条"), ("合同编通则解释", "第六十五条")}]
    items = [{"kind": "passage", "text": f"《{a['title']}》{a['article_no']}：{a['text']}"} for a in pick]
    for c in tp["template"][:6]:
        items += [{"kind": "passage", "text": clause_text(c)}, {"kind": "passage", "text": clause_text(c["zh"])},
                  {"kind": "passage", "text": clause_text(c["en"])}]
    items += [{"kind": "passage", "text": clause_text(tp["unrelated"][0])},
              {"kind": "query", "text": "付款条件是否对买方不利？"},
              {"kind": "query", "text": "Is the liability cap reasonable for the buyer?"}]
    eng = Engine(variant, 4)
    vecs = []
    for it in items:
        vecs.append(eng.embed([it["text"]], it["kind"])[0])
    data = {"model_variant": variant, "dim": int(vecs[0].shape[0]), "max_tokens": MAX_TOKENS,
            "prefix": {"query": VARIANTS[variant][3], "passage": VARIANTS[variant][4]}, "pooling": VARIANTS[variant][2],
            "normalize": "l2", "items": [{**it, "vector": [round(float(x), 6) for x in v]} for it, v in zip(items, vecs)]}
    (OUT / f"reference-{variant}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print("reference", variant, len(items), data["dim"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("variant")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--chars", type=int, default=10000)
    a = ap.parse_args()
    if a.cmd == "accuracy":
        accuracy(a.variant, a.threads)
    elif a.cmd == "perf":
        perf(a.variant, a.threads, a.chars)
    elif a.cmd == "appmem":
        appmem(a.variant, a.threads)
    elif a.cmd == "pairdetail":
        pairdetail(a.variant, a.threads)
    elif a.cmd == "reference":
        reference(a.variant)
    else:
        raise SystemExit(a.cmd)
