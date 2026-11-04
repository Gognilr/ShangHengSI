"""断网翻译容器：单段任务、用时加载、空闲卸载，日志不含正文。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

ROOT = Path(os.environ.get('CONVERT_SPOOL_DIR', '/spool'))
MODELS = Path('/srv/translator/models/mt')
MODEL = 'HY-MT2-7B-Q6_K.gguf'
LANGUAGES = {'中文': '中文', '英文': '英语', '日文': '日语', '韩文': '韩语', '俄文': '俄语',
             '西班牙文': '西班牙语', '法文': '法语', '德文': '德语', '阿拉伯文': '阿拉伯语'}


def write(path, value):
    part = path.with_suffix('.part')
    part.write_text(value, encoding='utf-8')
    part.replace(path)


def verify():
    for line in (MODELS / 'MANIFEST.sha256').read_text().splitlines():
        digest, name = line.split()
        with (MODELS / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('manifest')


def alive():
    try:
        return 0 <= time.time() - (ROOT / 'translator.alive').stat().st_mtime <= 30
    except OSError:
        return False


def heartbeat():
    while True:
        try:
            (ROOT / 'translator.alive').touch(mode=0o660)
            # worker 删除请求后，迟到结果只保留十分钟，避免内存盘残留。
            for path in (ROOT / 'mt/out').iterdir():
                if time.time() - path.stat().st_mtime > 600:
                    path.unlink(missing_ok=True)
        except OSError:
            pass
        time.sleep(5)


def pause(request, loading):
    while True:
        if not request.exists():
            return False
        now = time.time()
        markers = [ROOT / 'asr/pause', *(ROOT / 'mt/pause').glob('*')]
        busy = False
        for marker in markers:
            try:
                busy |= 0 <= now - marker.stat().st_mtime < 600
            except OSError:
                pass
        raw = os.environ.get('MT_MEM', '12g').lower()
        required = int(float(raw.rstrip('gmk')) * {'g': 1024**3, 'm': 1024**2, 'k': 1024}.get(raw[-1], 1))
        available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        if not busy and (not loading or available >= required):
            return True
        time.sleep(1)


class Engine:
    def __init__(self):
        self.process = None

    def close(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        threads = int(os.environ.get('MT_THREADS', '4'))
        self.process = subprocess.Popen(['llama-server', '-m', str(MODELS / MODEL), '--host', '127.0.0.1',
            '--port', '8080', '-c', '8192', '-t', str(threads), '-tb', str(threads), '-np', '1',
            '--no-warmup', '--no-webui'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(.5)
        self.close()
        raise RuntimeError('load')

    def translate(self, text, target):
        self.start()
        request = urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions', headers={'Content-Type': 'application/json'},
            data=json.dumps({'messages': [{'role': 'user', 'content': f'将以下文本翻译为{LANGUAGES[target]}，注意只需要输出翻译后的结果，不要额外解释：\n{text}'}],
                             'temperature': 0, 'max_tokens': 2048, 'stream': False, 'cache_prompt': False}).encode())
        with urllib.request.urlopen(request, timeout=1800) as response:
            result = json.load(response)['choices'][0]
        if result['finish_reason'] != 'stop' or not result['message']['content'].strip():
            raise ValueError('incomplete')
        return result['message']['content']


def main():
    os.umask(0o007)
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if args.check:
        raise SystemExit(0 if alive() else 1)
    verify()
    engine = Engine()
    if args.smoke:
        try:
            assert engine.translate('请在验收合格后支付余款。', '英文')
            print('本地翻译断网冒烟完成')
        finally:
            engine.close()
        return
    for directory in ('in', 'out', 'pause'):
        (ROOT / 'mt' / directory).mkdir(parents=True, exist_ok=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    last = time.monotonic()
    while True:
        requests = sorted((ROOT / 'mt/in').glob('*.json'), key=lambda path: path.stat().st_mtime)
        if not requests:
            if time.monotonic() - last > 300:
                engine.close()
            time.sleep(.2)
            continue
        request = requests[0]
        result = ROOT / 'mt/out' / request.name
        try:
            if not pause(request, engine.process is None):
                continue
            options = json.loads(request.read_text(encoding='utf-8'))
            text, target = options['text'], options['target']
            if not isinstance(text, str) or not 1 <= len(text) <= 800 or target not in LANGUAGES:
                raise ValueError('request')
            translated = engine.translate(text, target)
            if request.exists():
                write(result, json.dumps({'translation': translated}, ensure_ascii=False))
        except Exception:
            if request.exists():
                write(result.with_suffix('.err'), 'FAILED')
            engine.close()
        finally:
            request.unlink(missing_ok=True)
            last = time.monotonic()


if __name__ == '__main__':
    main()
