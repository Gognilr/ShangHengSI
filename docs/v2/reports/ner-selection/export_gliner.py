"""GLiNER 原始权重本地导出；配置只在评测副本中指向本地分词器。"""
import argparse
import json
import shutil
from pathlib import Path

import torch
from gliner import GLiNER

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("encoder", type=Path)
parser.add_argument("out", type=Path)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(2)
config = json.loads((args.source / "gliner_config.json").read_text())
config["model_name"] = str(args.encoder)
(args.out / "gliner_config.json").write_text(json.dumps(config), encoding="utf-8")
if not (args.out / "model.safetensors").exists():
    shutil.copyfile(args.source / "model.safetensors", args.out / "model.safetensors")
model = GLiNER.from_pretrained(str(args.out), local_files_only=True, map_location="cpu", strict=True,
                               _attn_implementation="eager")
model.eval()
print("loaded", type(model).__name__, flush=True)
print("splitter", type(model.data_processor.words_splitter).__name__, flush=True)
model.data_processor.transformer_tokenizer.save_pretrained(str(args.out))
print(model.export_to_onnx(str(args.out), onnx_filename="model.onnx", quantized_filename="model_int8.onnx",
                           quantize=True, opset=19), flush=True)
print(json.dumps({"files": {p.name: p.stat().st_size for p in args.out.iterdir() if p.is_file()}}))
