"""断网转写服务：只通过 /spool 交换文件，日志不含录音内容。"""
import argparse
import hashlib
import json
import os
import shutil
import multiprocessing
import time
from pathlib import Path

ROOT = Path(os.environ.get('CONVERT_SPOOL_DIR', '/spool'))
MODELS = Path('/srv/transcriber/models/asr')


def write(path, value):
    part = path.with_name(path.name + '.part')
    part.write_text(value, encoding='utf-8')
    part.replace(path)


def verify():
    manifest = MODELS / 'MANIFEST.sha256'
    for line in manifest.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        digest, name = line.split(maxsplit=1)
        with (MODELS / name.lstrip('*')).open('rb') as src:
            if hashlib.file_digest(src, 'sha256').hexdigest() != digest:
                raise ValueError('manifest')


def alive():
    try:
        return time.time() - (ROOT / 'transcriber.alive').stat().st_mtime < 30
    except OSError:
        return False


def cleanup_outputs():
    for path in (ROOT / 'asr/out').iterdir():
        try:
            if time.time() - path.stat().st_mtime > 600:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass  # worker 可以同时取走输出。


def _heartbeat(interval):
    while True:
        try:
            (ROOT / 'transcriber.alive').touch(mode=0o660)
            cleanup_outputs()
        except OSError:
            pass  # 一次失败不终止心跳；内存盘恢复后继续。
        time.sleep(interval)


def start_heartbeat(interval=5):
    # 说话人处理等原生调用可能长时间占住 GIL，不能用同进程 Python 线程续心跳。
    process = multiprocessing.Process(target=_heartbeat, args=(interval,), daemon=True)
    process.start()
    return process


def pause(progress, deadline):
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError
        p = ROOT / 'asr/pause'
        try:
            paused = time.time() - p.stat().st_mtime <= 600
        except FileNotFoundError:
            paused = False
        if not paused:
            return
        time.sleep(1)


def wait_memory(progress, deadline):
    raw = os.environ.get('ASR_MEM', '4g').lower()
    required = int(float(raw.rstrip('gmk')) * {'g': 1024**3, 'm': 1024**2, 'k': 1024}.get(raw[-1], 1))
    while True:
        available = next(int(l.split()[1]) * 1024 for l in Path('/proc/meminfo').read_text().splitlines()
                         if l.startswith('MemAvailable:'))
        if available >= required:
            return
        write(progress, 'waiting_memory')
        if time.monotonic() >= deadline:
            raise TimeoutError('memory')
        time.sleep(min(30, max(0, deadline - time.monotonic())))


class Service:
    def __init__(self, fake=False):
        if fake and not Path('/srv/transcriber/TEST_IMAGE').exists():
            raise ValueError('fake requires test image')
        self.fake, self.engine, self.last_used = fake, None, time.monotonic()
        for sub in ('in', 'work', 'out', 'progress'):
            (ROOT / 'asr' / sub).mkdir(parents=True, exist_ok=True)
        # 单进程服务重启后不接续半个任务，worker 会从回放副本重新投递。
        for path in (ROOT / 'asr/work').iterdir():
            if path.is_dir():
                shutil.rmtree(path)

    def tick(self):
        base = ROOT / 'asr'
        cleanup_outputs()
        requests = sorted((p for p in (base / 'in').glob('*.json') if not p.name.endswith('.fake.json')),
                          key=lambda p: p.stat().st_mtime)
        if not requests:
            if self.engine is not None and time.monotonic() - self.last_used > 300:
                self.engine = None
                import gc
                gc.collect()
            return
        req = requests[0]
        token = req.stem
        work = base / 'work' / token
        progress = base / 'progress' / token
        work.mkdir(exist_ok=True)
        try:
            wav = work / 'input.wav'
            (base / 'in' / f'{token}.wav').replace(wav)
            options = json.loads(req.read_text())
            speakers = options.get('speakers')
            if speakers is not None and (type(speakers) is not int or not 2 <= speakers <= 10):
                raise ValueError('speakers')
            import wave
            with wave.open(str(wav)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            deadline = time.monotonic() + duration * 2 + 1800
            wait_memory(progress, deadline)
            pause(progress, deadline)
            if self.fake:
                result = json.loads(req.with_suffix('.fake.json').read_text())
                write(progress, str(result['duration_ms']))
            else:
                from inference import Engine
                if self.engine is None:
                    self.engine = Engine(str(MODELS), int(os.environ.get('ASR_THREADS', '8')))
                def boundary():
                    if not req.exists():
                        raise RuntimeError('cancelled')
                    pause(progress, deadline)
                result = self.engine.run(str(wav), speakers, boundary,
                                         lambda ms: write(progress, str(ms)))
            if time.monotonic() >= deadline:
                raise TimeoutError
            write(base / 'out' / f'{token}.json', json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            code = ('TIMEOUT_MEMORY' if str(exc) == 'memory' else 'TIMEOUT') if isinstance(exc, TimeoutError) else 'FAILED'
            write(base / 'out' / f'{token}.err', code)
        finally:
            req.unlink(missing_ok=True)
            req.with_suffix('.fake.json').unlink(missing_ok=True)
            shutil.rmtree(work)
            self.last_used = time.monotonic()


def main():
    os.umask(0o007)
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--fake', action='store_true')
    args = ap.parse_args()
    if args.check:
        raise SystemExit(0 if alive() else 1)
    if not args.fake:
        verify()
    if args.smoke:
        from inference import Engine
        Engine(str(MODELS), int(os.environ.get('ASR_THREADS', '8'))).smoke()
        return
    service = Service(args.fake)
    heartbeat = start_heartbeat()
    while True:
        if not heartbeat.is_alive():
            heartbeat = start_heartbeat()
        service.tick()
        time.sleep(.2)


if __name__ == '__main__':
    main()
