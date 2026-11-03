"""把 FireRedPunc（BERT 标点模型）导出为 INT8 ONNX（V31-M-01）。

只在仓库外的一次性容器中运行（需要 torch、transformers 与 FireRedASR2S 源码），
任何镜像都不装 torch。产物：punc.int8.onnx（输入 input_ids、attention_mask，
已含开头的 [CLS]；输出 logits，形状 (N, T, 5)，去掉 [CLS] 位置后按 out_dict 取标点）。

用法：
  PYTHONPATH=<FireRedASR2S 源码> python export_punc_onnx.py <FireRedPunc 目录> <输出目录>
"""
import os
import sys

import numpy as np
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
import onnxruntime as ort

from fireredasr2s.fireredpunc.punc import ModelIO, load_punc_bert_model


class Wrapper(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.bert = model.bert
        self.classifier = model.classifier

    def forward(self, input_ids, attention_mask):
        out = self.bert(input_ids=input_ids, attention_mask=attention_mask)[0]
        return self.classifier(out)


def main(model_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    model = load_punc_bert_model(model_dir).eval()
    wrapper = Wrapper(model).eval()
    io = ModelIO(model_dir)
    cls_id = model.cls_id

    samples = ["今天评审和杭州的设备采购合同乙先介绍一下背景",
               "对方报价含税一共三百二十八万六千元单台一百六十四万三千元",
               "好的 ON TIME 叫准时我们下周三之前发出"]
    ids, lengths, _ = io.text2tensor(samples)
    ids = torch.cat([torch.full((ids.size(0), 1), cls_id, dtype=torch.long), ids], dim=1)
    mask = torch.zeros_like(ids, dtype=torch.float)
    for i, n in enumerate(lengths.tolist()):
        mask[i, :n + 1] = 1

    fp32 = os.path.join(out_dir, "punc.fp32.onnx")
    int8 = os.path.join(out_dir, "punc.int8.onnx")
    torch.onnx.export(
        wrapper, (ids, mask), fp32, opset_version=17,
        input_names=["input_ids", "attention_mask"], output_names=["logits"],
        dynamic_axes={"input_ids": {0: "N", 1: "T"}, "attention_mask": {0: "N", 1: "T"},
                      "logits": {0: "N", 1: "T"}},
        dynamo=False)
    quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)

    with torch.no_grad():
        ref = wrapper(ids, mask).argmax(-1).numpy()
    for path in (fp32, int8):
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        got = sess.run(None, {"input_ids": ids.numpy(), "attention_mask": mask.numpy()})[0].argmax(-1)
        valid = mask.numpy() > 0
        agree = float((got == ref)[valid].mean())
        print(os.path.basename(path), os.path.getsize(path), "argmax agreement", round(agree, 4))
    os.remove(fp32)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
