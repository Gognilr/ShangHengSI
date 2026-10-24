"""开发机8 CPU断网预检；单独进程测量导入与模型的峰值增量。"""
import json
import resource
import statistics
import time
from pathlib import Path

BASE = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
from app.redaction import ner

ner.check(load=True)
records = json.loads(Path('/evidence/corpus.json').read_text())['records']
source = '\n'.join(r['input'] for r in records if r['origin'] == 'narrative')
text = (source * (10000 // len(source) + 1))[:10000]
ner.recognize(text)
timings = []
for _ in range(3):
    start = time.perf_counter()
    ner.recognize(text)
    timings.append(time.perf_counter() - start)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
result = {'threads': ner._threads, 'chars': len(text), 'seconds': timings,
          'median_seconds': statistics.median(timings), 'baseline_bytes': BASE,
          'peak_bytes': peak, 'increase_bytes': peak - BASE}
print(json.dumps(result), flush=True)
assert result['median_seconds'] <= 10
assert result['increase_bytes'] <= 2_500_000_000
