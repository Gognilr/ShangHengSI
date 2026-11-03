"""V31-M-01：用 FireRedASR2-AED 原始 FP32 权重（torch）按同一分段、同样贪心解码转写，
给 INT8 字错率做对照（design/V31-M.md 1.4 第 1 条）。只在仓库外的一次性 torch 容器中运行。

  PYTHONPATH=<FireRedASR2S 源码> python m01_fp32_ref.py in.wav eval.json out.json <AED 目录>
"""
import json
import sys
import wave

import numpy as np

from fireredasr2s.fireredasr2.asr import FireRedAsr2, FireRedAsr2Config


def main(wav_path, eval_json, out_json, model_dir):
    with wave.open(wav_path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    segs = json.load(open(eval_json, encoding="utf-8"))["segments"]
    cfg = FireRedAsr2Config(use_gpu=False, beam_size=1, nbest=1)
    asr = FireRedAsr2.from_pretrained("aed", model_dir, cfg)
    out = []
    for k, s in enumerate(segs):
        part = x[s["start_ms"] * 16:s["end_ms"] * 16]
        r = asr.transcribe([f"s{k}"], [(16000, part)])[0]
        out.append({"start_ms": s["start_ms"], "end_ms": s["end_ms"], "raw": r.get("text", "")})
    json.dump({"segments": out}, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:5])
