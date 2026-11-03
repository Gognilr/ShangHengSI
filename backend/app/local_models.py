"""本地模型清单与离线检查；重型模型绝不在 app / worker 中加载。"""
import importlib.metadata
import os
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def catalog_path() -> Path:
    configured = os.environ.get('LOCAL_MODELS_FILE')
    return Path(configured) if configured else ROOT.parent / 'deploy/models.yaml'


def _manifest(model: dict) -> Path:
    if model.get('group'):
        deployed = catalog_path().parent / 'model-manifests' / (model['group'] + '.sha256')
        if deployed.exists():
            return deployed
    return ROOT.parent / model['dir'] / 'MANIFEST.sha256'


def _light_check(mid: str, smoke: bool) -> None:
    if mid == 'ner':
        from app.redaction import ner
        ner.check(load=smoke)
        if smoke:
            ner.recognize('张三代表虚构公司签署合同。')
    elif mid == 'embedding':
        from app.embedding import engine
        engine.check(load=smoke)
        if smoke:
            engine.embed(['虚构合同条款'], 'passage')
    else:
        import rapidocr_onnxruntime as rapid
        if importlib.metadata.version('rapidocr-onnxruntime') != '1.4.4':
            raise ValueError
        if len(list((Path(rapid.__file__).parent / 'models').glob('*.onnx'))) != 3:
            raise ValueError
        if smoke:
            import cv2
            import numpy as np
            image = np.full((120, 600, 3), 255, dtype=np.uint8)
            cv2.putText(image, 'TEST 123', (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
            result, _ = rapid.RapidOCR()(image)
            if not result:
                raise ValueError


# 状态页短时复用轻量文件核验，避免每次刷新读取 GB 级模型。CLI 每次重新核验。
_checks: dict[str, tuple[float, bool]] = {}


def inspect_models(model_id: str | None = None, *, smoke: bool = False, cached: bool = False) -> list[dict]:
    models = yaml.safe_load(catalog_path().read_text(encoding='utf-8'))
    if model_id is not None:
        models = [m for m in models if m['id'] == model_id]
        if not models:
            raise ValueError('未知模型')
    profiles = set(os.environ.get('COMPOSE_PROFILES', '').split(','))
    rows = []
    for model in models:
        group = model.get('group')
        enabled = not group or group in profiles
        verification = 'unavailable'
        ok = False
        try:
            if group:
                # 这里只验证小型清单。心跳由独立容器校验模型后写入，不把清单存在冒充模型已校验。
                entries = dict(line.split()[::-1] for line in _manifest(model).read_text(encoding='ascii').splitlines())
                for name in model['files']:
                    if len(entries[name]) != 64 or len(bytes.fromhex(entries[name])) != 32:
                        raise ValueError
                verification = 'manifest_only'
                alive = Path(os.environ.get('CONVERT_SPOOL_DIR', '/spool')) / 'transcriber.alive'
                ok = enabled and 0 <= time.time() - alive.stat().st_mtime <= 30
                if ok:
                    verification = 'container_verified'
            else:
                previous = _checks.get(model['id'])
                if cached and previous and time.monotonic() - previous[0] < 60:
                    ok = previous[1]
                else:
                    _light_check(model['id'], smoke)
                    ok = True
                    _checks[model['id']] = (time.monotonic(), ok)
                verification = 'package_verified' if model['id'] == 'ocr' else 'sha256_verified'
        except Exception:  # 不返回模型路径、异常详情或输入内容。
            ok = False
        rows.append(dict(id=model['id'], purpose=model['purpose'], version=model['source'],
                         verification=verification, status=('disabled' if not enabled else 'enabled' if ok else 'unavailable')))
    return rows
