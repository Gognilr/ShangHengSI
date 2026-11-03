"""模拟结果验证交换、让路、进度与超时；不加载模型。"""
import json
import tempfile
import threading
import time
import unittest
import wave
import ctypes
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import main


def observe_heartbeat(path, output, ready):
    seen = set()
    deadline = time.monotonic() + 1.5
    ready.set()
    while time.monotonic() < deadline:
        if path.exists():
            seen.add(path.stat().st_mtime_ns)
        time.sleep(.03)
    output.write_text(str(len(seen)))


class ExchangeTest(unittest.TestCase):
    def test_exchange_pause_progress_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'ROOT', Path(directory)):
            with patch.object(Path, 'exists', return_value=True):
                service = main.Service(fake=True)
            base = main.ROOT / 'asr'
            token = 'a' * 32
            wav = base / 'in' / f'{token}.wav'
            with wave.open(str(wav), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b'\0\0' * 16000)
            req = wav.with_suffix('.json')
            req.write_text('{"speakers":2}')
            result = {'duration_ms':1000, 'segments':[]}
            req.with_suffix('.fake.json').write_text(json.dumps(result))
            (base / 'pause').touch()
            with patch.object(main, 'wait_memory'):
                thread = threading.Thread(target=service.tick)
                thread.start()
                time.sleep(.05)
                self.assertFalse((base / 'out' / f'{token}.json').exists())
                (base / 'pause').unlink()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(json.loads((base / 'out' / f'{token}.json').read_text()), result)
            self.assertEqual((base / 'progress' / token).read_text(), '1000')
            self.assertFalse(wav.exists())
            self.assertEqual(list((base / 'work').iterdir()), [])

    def test_timeout_and_fake_restricted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'ROOT', Path(directory)):
            with patch.object(Path, 'exists', return_value=False):
                with self.assertRaises(ValueError):
                    main.Service(fake=True)
            with self.assertRaises(TimeoutError):
                main.pause(Path(directory) / 'progress', time.monotonic() - 1)

    def test_heartbeat_survives_native_gil_hold(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'ROOT', Path(directory)):
            (main.ROOT / 'asr/out').mkdir(parents=True)
            heartbeat = main.start_heartbeat(.05)
            ready = multiprocessing.Event()
            output = main.ROOT / 'observed'
            observer = multiprocessing.Process(target=observe_heartbeat,
                         args=(main.ROOT / 'transcriber.alive', output, ready))
            observer.start()
            try:
                self.assertTrue(ready.wait(3))
                # PyDLL 调用保留 GIL，复现长原生调用阻塞 Python 线程的情形。
                ctypes.PyDLL(None).usleep(2_000_000)
                observer.join(3)
                self.assertGreater(int(output.read_text()), 10)
            finally:
                heartbeat.terminate(); heartbeat.join(3)
                if observer.is_alive():
                    observer.terminate(); observer.join(3)


if __name__ == '__main__':
    unittest.main()
