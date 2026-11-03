"""V31-M-01 选型实测：语音分段 → 转写 → 标点 → 说话人，输出 design/V31-M.md 4.2 的 JSON。

只用于选型（不进镜像）：onnxruntime、sherpa-onnx、kaldi-native-fbank、numpy，无 torch。
FireRedVAD 的后处理直接引用其源码（vad_postprocessor.py 只依赖 numpy），运行时以
PYTHONPATH 指向 FireRedVAD 仓库。输入为 16 kHz 单声道 16 位 WAV。

  python m01_eval.py in.wav out.json --models /m --asr aed --vad firered --threads 8 [--speakers 4]
"""
import argparse
import json
import os
import struct
import time
import wave

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as ort
import sherpa_onnx

AED = "sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26"
CTC = "sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25"
MAX_SEG_S = 30


def read_wav(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def fbank(samples, snip_edges=True):
    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = 16000
    opts.frame_opts.dither = 0
    opts.frame_opts.snip_edges = snip_edges
    opts.mel_opts.num_bins = 80
    fb = knf.OnlineFbank(opts)
    frames, got = [], 0
    for k in range(0, len(samples), 16000 * 60):  # 分块送入，避免长录音生成巨大的列表
        fb.accept_waveform(16000, (samples[k:k + 16000 * 60] * 32768).tolist())
        if k + 16000 * 60 >= len(samples):
            fb.input_finished()
        n = fb.num_frames_ready
        frames += [np.asarray(fb.get_frame(i), np.float32) for i in range(got, n)]
        got = n
    return np.stack(frames) if frames else np.zeros((0, 80), np.float32)


def read_kaldi_cmvn(path):
    data = open(path, "rb").read()
    i = data.index(b"DM ") + 3
    rows = struct.unpack("<i", data[i + 1:i + 5])[0]
    cols = struct.unpack("<i", data[i + 6:i + 10])[0]
    stats = np.frombuffer(data[i + 10:i + 10 + 8 * rows * cols], dtype="<f8").reshape(rows, cols)
    count = stats[0, -1]
    mean = stats[0, :-1] / count
    var = np.maximum(stats[1, :-1] / count - mean * mean, 1e-20)
    return mean, 1 / np.sqrt(var)


def session(path, threads):
    o = ort.SessionOptions()
    o.intra_op_num_threads = threads
    o.inter_op_num_threads = 1
    return ort.InferenceSession(path, o, providers=["CPUExecutionProvider"])


# ---------- 语音分段 ----------

def vad_firered(samples, models, threads):
    from fireredvad.core.vad_postprocessor import VadPostprocessor
    mean, istd = read_kaldi_cmvn(os.path.join(models, "cmvn.ark"))
    sess = session(os.path.join(models, "fireredvad_vad.onnx"), threads)
    feat = ((fbank(samples) - mean) * istd).astype(np.float32)
    probs = []
    for k in range(0, len(feat), 30000):
        probs.append(sess.run(None, {sess.get_inputs()[0].name: feat[None, k:k + 30000]})[0].reshape(-1))
    probs = np.concatenate(probs)
    post = VadPostprocessor(5, 0.4, 20, MAX_SEG_S * 100, 20, 0, 0)
    segs = post.decision_to_segment(post.process(probs.tolist()), len(samples) / 16000)
    return [(int(s * 16000), int(e * 16000)) for s, e in segs]


def vad_silero(samples, models, threads):
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = os.path.join(models, "silero_vad.onnx")
    cfg.silero_vad.min_silence_duration = 0.25
    cfg.silero_vad.max_speech_duration = MAX_SEG_S
    cfg.sample_rate = 16000
    cfg.num_threads = threads
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=MAX_SEG_S + 10)
    out = []
    w = cfg.silero_vad.window_size
    for k in range(0, len(samples), w):
        vad.accept_waveform(samples[k:k + w])
        while not vad.empty():
            out.append((vad.front.start, vad.front.start + len(vad.front.samples)))
            vad.pop()
    vad.flush()
    while not vad.empty():
        out.append((vad.front.start, vad.front.start + len(vad.front.samples)))
        vad.pop()
    return out


# ---------- 转写 ----------

class AedDecoder:
    """FireRedASR2-AED 贪心解码（同 sherpa-onnx 的做法），另外记录每个词元的概率。"""

    def __init__(self, models, threads, dec_threads):
        d = os.path.join(models, AED)
        self.enc = session(os.path.join(d, "encoder.int8.onnx"), threads)
        self.dec = session(os.path.join(d, "decoder.int8.onnx"), dec_threads or threads)
        self.t_enc = self.t_dec = 0.0
        m = self.enc.get_modelmeta().custom_metadata_map
        self.mean = np.array(m["cmvn_mean"].split(","), np.float32)
        self.istd = np.array(m["cmvn_inv_stddev"].split(","), np.float32)
        self.sos, self.eos = int(m["sos"]), int(m["eos"])
        self.layers, self.heads, self.hdim = int(m["num_decoder_layers"]), int(m["num_head"]), int(m["head_dim"])
        self.max_len = int(m["max_len"])
        self.tokens = {}
        for line in open(os.path.join(d, "tokens.txt"), encoding="utf-8"):
            sym, i = line.rstrip("\n").rsplit(" ", 1)
            self.tokens[int(i)] = sym

    def __call__(self, samples):
        feat = ((fbank(samples, snip_edges=False) - self.mean) * self.istd).astype(np.float32)
        t = time.time()
        ck, cv = self.enc.run(None, {"x": feat[None], "x_len": np.array([len(feat)], np.int64)})
        self.t_enc += time.time() - t
        t = time.time()
        n_max = min(int(len(feat) / 100 * 12), self.max_len // 2)
        cache = (self.layers, 1, n_max + 4, self.heads, self.hdim)
        sk, sv = np.zeros(cache, np.float32), np.zeros(cache, np.float32)
        tok, out = self.sos, []
        for i in range(n_max):
            logits, sk, sv = self.dec.run(None, {
                "tokens": np.array([[tok]], np.int64), "in_n_layer_self_k_cache": sk,
                "in_n_layer_self_v_cache": sv, "n_layer_cross_k": ck, "n_layer_cross_v": cv,
                "offset": np.array([i], np.int64)})
            z = logits.reshape(-1)
            tok = int(z.argmax())
            if tok == self.eos:
                break
            p = float(np.exp(z[tok] - z.max()) / np.exp(z - z.max()).sum())
            out.append((self.tokens.get(tok, ""), p))
        self.t_dec += time.time() - t
        text, conf = "", []
        for sym, p in out:
            piece = sym.replace("▁", " ")
            if piece.startswith(" ") and not text:
                piece = piece.lstrip()
            text += piece
            conf += [p] * len(piece)
        return text, conf


class CtcRecognizer:
    def __init__(self, models, threads):
        d = os.path.join(models, CTC)
        self.r = sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(
            model=os.path.join(d, "model.int8.onnx"), tokens=os.path.join(d, "tokens.txt"), num_threads=threads)

    def __call__(self, samples):
        st = self.r.create_stream()
        st.accept_waveform(16000, samples)
        self.r.decode_stream(st)
        return st.result.text, None


# ---------- 标点 ----------

class Punc:
    def __init__(self, models, threads):
        d = os.path.join(models, "punc")
        self.sess = session(os.path.join(d, "punc.int8.onnx"), threads)
        self.vocab = {w.rstrip("\n"): i for i, w in enumerate(open(os.path.join(d, "vocab.txt"), encoding="utf-8"))}
        self.tags = [l.rsplit(" ", 1)[0] for l in open(os.path.join(d, "out_dict"), encoding="utf-8").read().splitlines()]

    def _wordpiece(self, word):
        pieces, start = [], 0
        while start < len(word):
            end = len(word)
            while end > start:
                sub = ("##" if start else "") + word[start:end]
                if sub in self.vocab:
                    break
                end -= 1
            if end == start:
                return ["[UNK]"]
            pieces.append(("##" if start else "") + word[start:end])
            start = end
        return pieces

    def __call__(self, text):
        """返回加标点后的文字，以及原文每个字符在新文字中的位置。"""
        units = []  # (原文起点, 原文终点, 词元)
        i = 0
        while i < len(text):
            c = text[i]
            if c.isspace():
                i += 1
                continue
            if c.isascii() and (c.isalnum() or c == "'"):
                j = i
                while j < len(text) and text[j].isascii() and (text[j].isalnum() or text[j] == "'"):
                    j += 1
                for p in self._wordpiece(text[i:j].lower()):
                    units.append((i, j, p))
                i = j
            else:
                units.append((i, i + 1, c))
                i += 1
        if not units:
            return text, list(range(len(text)))
        ids = [self.vocab["[CLS]"]] + [self.vocab.get(u[2], self.vocab["[UNK]"]) for u in units]
        ids = np.array([ids[:512]], np.int64)
        logits = self.sess.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids, np.float32)})[0][0, 1:]
        pred = logits.argmax(-1).tolist() + [0] * max(0, len(units) - logits.shape[0])
        # 同一原文区间（英文词拆成多个词元）只在最后一个词元后加标点
        end_tag = {}
        for (s, e, _), p in zip(units, pred):
            end_tag[e] = p
        out, pos = "", []
        for k, c in enumerate(text):
            pos.append(len(out))
            out += c
            t = end_tag.get(k + 1, 0)
            if t and (k + 1 == len(text) or not text[k + 1].isascii() or not text[k].isascii() or text[k + 1] == " "):
                out += self.tags[t]
                end_tag.pop(k + 1)
        if out and out[-1] not in "，。？！":
            out += "。"
        return out, pos


# ---------- 说话人 ----------

def diarize(samples, models, threads, speakers, embed):
    c = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=os.path.join(models, "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx")),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=os.path.join(models, embed), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers or -1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    sd = sherpa_onnx.OfflineSpeakerDiarization(c)
    return [(int(r.start * 16000), int(r.end * 16000), r.speaker) for r in sd.process(samples).sort_by_start_time()]


def assign_speaker(seg, turns):
    s, e = seg
    best, who = 0, 0
    acc = {}
    for ts, te, spk in turns:
        ov = min(e, te) - max(s, ts)
        if ov > 0:
            acc[spk] = acc.get(spk, 0) + ov
    for spk, ov in acc.items():
        if ov > best:
            best, who = ov, spk
    return who + 1


ANON_PEAK = [0]


def sample_anon():
    """每 0.5 秒读一次 cgroup 的 anon（不含页缓存），记峰值。"""
    import threading

    def run():
        while True:
            try:
                for line in open("/sys/fs/cgroup/memory.stat"):
                    if line.startswith("anon "):
                        ANON_PEAK[0] = max(ANON_PEAK[0], int(line.split()[1]))
                        break
            except OSError:
                return
            time.sleep(0.5)
    threading.Thread(target=run, daemon=True).start()


def peak_mem():
    for p in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        if os.path.exists(p):
            return int(open(p).read())
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("out")
    ap.add_argument("--models", default="/m")
    ap.add_argument("--asr", choices=["aed", "ctc"], default="aed")
    ap.add_argument("--vad", choices=["firered", "silero"], default="firered")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--speakers", type=int, default=0)
    ap.add_argument("--embed", default="3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx")
    ap.add_argument("--dec-threads", type=int, default=0)
    ap.add_argument("--no-diar", action="store_true")
    ap.add_argument("--diar-only", action="store_true")
    a = ap.parse_args()

    sample_anon()
    t0 = time.time()
    samples = read_wav(a.wav)
    if a.diar_only:
        t = time.time()
        turns = diarize(samples, a.models, a.threads, a.speakers, a.embed)
        print(json.dumps({"diar_s": round(time.time() - t, 1), "turns": len(turns),
                          "anon_peak": ANON_PEAK[0], "peak_mem": peak_mem()}))
        return
    dur = len(samples) / 16000
    timing = {}

    t = time.time()
    segs = (vad_firered if a.vad == "firered" else vad_silero)(samples, a.models, a.threads)
    timing["vad"] = time.time() - t

    t = time.time()
    asr = AedDecoder(a.models, a.threads, a.dec_threads) if a.asr == "aed" else CtcRecognizer(a.models, a.threads)
    punc = Punc(a.models, a.threads)
    timing["load"] = time.time() - t

    t = time.time()
    raw = [asr(samples[s:e]) for s, e in segs]
    timing["asr"] = time.time() - t
    if a.asr == "aed":
        timing["asr_enc"], timing["asr_dec"] = asr.t_enc, asr.t_dec

    t = time.time()
    punced = [punc(text) for text, _ in raw]
    timing["punc"] = time.time() - t

    turns = []
    if not a.no_diar:
        t = time.time()
        turns = diarize(samples, a.models, a.threads, a.speakers, a.embed)
        timing["diar"] = time.time() - t

    out = []
    for (s, e), (text, conf), (ptext, pos) in zip(segs, raw, punced):
        if not text.strip():
            continue
        item = {"start_ms": s // 16, "end_ms": e // 16, "speaker": assign_speaker((s, e), turns) if turns else 1,
                "text": ptext, "raw": text}
        if conf is not None:
            item["conf"] = [round(c, 4) for c in conf]
            item["pos"] = pos
        out.append(item)
    timing = {k: round(v, 1) for k, v in timing.items()}
    total = time.time() - t0
    result = {"duration_ms": int(dur * 1000), "asr": a.asr, "vad": a.vad, "threads": a.threads, "dec_threads": a.dec_threads,
              "embed": a.embed, "segments": out, "timing_s": timing, "total_s": round(total, 1),
              "rtf": round(total / dur, 3), "peak_mem": peak_mem(), "anon_peak": ANON_PEAK[0], "num_segments": len(segs),
              "max_seg_s": round(max((e - s for s, e in segs), default=0) / 16000, 1)}
    json.dump(result, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != "segments"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
