"""只在隔离评测镜像中，把已校验本地权重导出 ONNX；不联网。"""
import argparse
import json
from pathlib import Path

import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModelForTokenClassification, AutoTokenizer

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("out", type=Path)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(2)
tokenizer = AutoTokenizer.from_pretrained(str(args.source), local_files_only=True, trust_remote_code=False)
model = AutoModelForTokenClassification.from_pretrained(str(args.source), local_files_only=True,
                                                       trust_remote_code=False, attn_implementation="eager").eval()


class Wrapper(torch.nn.Module):
    def __init__(self, encoder):
        super().__init__()
        self.encoder = encoder

    def forward(self, input_ids, attention_mask, token_type_ids):
        return self.encoder(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids).logits


data = tokenizer("沈澈宁核对了海岚研究中心的检验安排。", return_tensors="pt")
names = ["input_ids", "attention_mask", "token_type_ids"]
torch.onnx.export(Wrapper(model), tuple(data[n] for n in names), str(args.out / "model.onnx"),
                  input_names=names, output_names=["logits"], opset_version=17,
                  dynamic_axes={n: {0: "batch", 1: "sequence"} for n in [*names, "logits"]})
tokenizer.save_pretrained(str(args.out))
model.config.to_json_file(str(args.out / "config.json"))
quantize_dynamic(str(args.out / "model.onnx"), str(args.out / "model_int8.onnx"),
                 weight_type=QuantType.QInt8, per_channel=True, reduce_range=True)
print(json.dumps({"files": {p.name: p.stat().st_size for p in args.out.iterdir() if p.is_file()}}))
