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


class AedDecoder:
    """FireRedASR2-AED 贪心解码（同 sherpa-onnx 的做法），另外记录每个词元的概率。"""

    def __init__(self, models, threads, dec_threads):
        d = models
        self.enc = session(os.path.join(d, "asr_encoder.int8.onnx"), threads)
        self.dec = session(os.path.join(d, "asr_decoder.int8.onnx"), dec_threads or threads)
        self.t_enc = self.t_dec = 0.0
        m = self.enc.get_modelmeta().custom_metadata_map
        self.mean = np.array(m["cmvn_mean"].split(","), np.float32)
        self.istd = np.array(m["cmvn_inv_stddev"].split(","), np.float32)
        self.sos, self.eos = int(m["sos"]), int(m["eos"])
        self.layers, self.heads, self.hdim = int(m["num_decoder_layers"]), int(m["num_head"]), int(m["head_dim"])
        self.max_len = int(m["max_len"])
        self.tokens = {}
        for line in open(os.path.join(d, "asr_tokens.txt"), encoding="utf-8"):
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


class Punc:
    def __init__(self, models, threads):
        d = models
        self.sess = session(os.path.join(d, "punc.int8.onnx"), threads)
        self.vocab = {w.rstrip("\n"): i for i, w in enumerate(open(os.path.join(d, "punc_vocab.txt"), encoding="utf-8"))}
        self.tags = [l.rsplit(" ", 1)[0] for l in open(os.path.join(d, "punc_out_dict"), encoding="utf-8").read().splitlines()]

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
        pred = []
        for start in range(0, len(units), 511):
            ids = np.array([[self.vocab['[CLS]']] + [self.vocab.get(u[2], self.vocab['[UNK]'])
                            for u in units[start:start + 511]]], np.int64)
            logits = self.sess.run(None, {"input_ids": ids, "attention_mask": np.ones_like(ids, np.float32)})[0][0, 1:]
            pred.extend(logits.argmax(-1).tolist())
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
                model=os.path.join(models, "seg.onnx")),
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


class Engine:
    def __init__(self, models, threads):
        self.models, self.threads = models, threads
        self.vad = session(os.path.join(models, 'vad.onnx'), threads)
        self.mean, self.istd = read_kaldi_cmvn(os.path.join(models, 'vad_cmvn.ark'))
        self.asr = AedDecoder(models, threads, 1)
        self.punc = Punc(models, threads)

    def speech(self, samples, boundary):
        from vad_postprocessor import VadPostprocessor
        probs = []
        for start in range(0, len(samples), 16000 * 300):
            boundary()
            feat = ((fbank(samples[start:start + 16000 * 300 + 400]) - self.mean) * self.istd).astype(np.float32)
            feat = feat[:30000]
            if len(feat):
                probs.extend(self.vad.run(None, {self.vad.get_inputs()[0].name: feat[None]})[0].reshape(-1).tolist())
        post = VadPostprocessor(5, .4, 20, 3000, 20, 0, 0)
        segs = post.decision_to_segment(post.process(probs), len(samples) / 16000)
        # 最后一段可能因帧尾多出 25ms，明确限制每段不超过 30 秒。
        return [(int(s * 16000) + off, min(int(e * 16000), int(s * 16000) + off + 480000))
                for s, e in segs for off in range(0, max(1, int((e-s)*16000)), 480000)]

    def run(self, wav, speakers, boundary, progress):
        samples = read_wav(wav)
        segs = self.speech(samples, boundary)
        out = []
        for s, e in segs:
            boundary()
            text, conf = self.asr(samples[s:e])
            ptext, positions = self.punc(text)
            low = []
            for index, probability in enumerate(conf):
                if probability < .5:
                    p = positions[index]
                    if low and low[-1][1] == p:
                        low[-1][1] += 1
                    else:
                        low.append([p, p + 1])
            if text.strip():
                out.append({'start_ms': s // 16, 'end_ms': e // 16, 'speaker': 1, 'text': ptext, 'low': low})
            progress(e // 16)
        boundary()
        turns = diarize(samples, self.models, self.threads, speakers, 'spk.onnx') if segs else []
        for item in out:
            item['speaker'] = assign_speaker((item['start_ms']*16, item['end_ms']*16), turns)
        progress(len(samples) // 16)
        return {'duration_ms': len(samples) // 16, 'segments': out}

    def smoke(self):
        samples = np.zeros(16000, np.float32)
        self.speech(samples, lambda: None)
        self.asr(samples)
        self.punc('测试文字')
        diarize(samples, self.models, self.threads, None, 'spk.onnx')



