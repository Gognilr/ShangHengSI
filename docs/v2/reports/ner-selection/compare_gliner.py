"""先核对 PyTorch 参考与 ONNX 导出的语义，避免把导出错误当成模型效果。"""
import argparse
import gc
import json
from pathlib import Path

import torch
from gliner import GLiNER
from run_gliner_onnx import GlinerRecognizer

parser = argparse.ArgumentParser()
parser.add_argument("folder", type=Path)
parser.add_argument("out", type=Path)
args = parser.parse_args()
torch.set_num_threads(2)
texts = ["Cristiano Ronaldo was born in Madeira, Portugal.",
         "沈澈宁核对了海岚研究中心的检验安排。",
         "Nora Vale reviewed the inspection notes for Aster Vale Research."]
labels = ["person", "organization", "street address"]
model = GLiNER.from_pretrained(str(args.folder), local_files_only=True, strict=True)
reference = [model.predict_entities(text, labels, threshold=0.01) for text in texts]
del model
gc.collect()
output = {"texts": texts, "pytorch": reference}
for filename in ("model.onnx", "model_int8.onnx"):
    model = GlinerRecognizer(args.folder, filename, "default")
    output[filename] = [model.recognize(text) for text in texts]
    del model
    gc.collect()
args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(output, ensure_ascii=False))
