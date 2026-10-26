"""V2-E-04a：bge-small-zh-v1.5 从官方 safetensors 导出 ONNX；三个候选做动态 INT8 量化（评测用，不是产品代码）。

在 aireview-ner-eval:b06a 镜像（已有 torch 2.5.1+cpu、transformers 4.57.3、onnx）中断网运行：
  python export_onnx.py export-bge-small /models/bge-small-zh /models/bge-small-zh/onnx/model.onnx
  python export_onnx.py quantize <fp32.onnx> <int8.onnx>
导出只输出 last_hidden_state，池化（CLS 或平均）与归一化放在评测脚本中，与产品引擎一致。
"""
import sys


def export_bge_small(src: str, dst: str) -> None:
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(src, local_files_only=True)
    model = AutoModel.from_pretrained(src, local_files_only=True).eval()
    enc = tok(["示例条款文本", "第二段"], padding=True, return_tensors="pt")
    with torch.no_grad():
        torch.onnx.export(
            model,
            (enc["input_ids"], enc["attention_mask"], enc["token_type_ids"]),
            dst,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["last_hidden_state"],
            dynamic_axes={k: {0: "batch", 1: "seq"} for k in ["input_ids", "attention_mask", "token_type_ids", "last_hidden_state"]},
            opset_version=17,
            do_constant_folding=True,
        )
        ref = model(**enc).last_hidden_state.numpy()
    import numpy as np
    import onnxruntime as ort

    sess = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
    out = sess.run(None, {k: v.numpy() for k, v in enc.items()})[0]
    print("export max abs diff", float(np.abs(out - ref).max()))


def quantize(src: str, dst: str) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(src, dst, weight_type=QuantType.QInt8, per_channel=True, reduce_range=False,
                     use_external_data_format=False)
    print("quantized", dst)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "export-bge-small":
        export_bge_small(sys.argv[2], sys.argv[3])
    elif cmd == "quantize":
        quantize(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(f"unknown command {cmd}")
