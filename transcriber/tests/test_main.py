"""模拟结果验证交换、让路、进度与超时；不加载模型。"""
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import main


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
            with self.assertRaises(ValueError):
                main.Service(fake=True)
            with self.assertRaises(TimeoutError):
                main.pause(Path(directory) / 'progress', time.monotonic() - 1)


if __name__ == '__main__':
    unittest.main()
