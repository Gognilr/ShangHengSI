"""U5-04：独立容器内运行当前脱敏主线，不使用现有服务或数据库。"""
import json
import hashlib
import os
import resource
import sys
import time
from pathlib import Path

from app.redaction import ner
from app.redaction.engine import NodeIn, compute
from app.chat.service import _redact

root = Path('/evidence')
mode = os.environ.get('PROBE_MODE', 'body')
ner.check(load=True)
clauses = [
    '青岚虚构化工有限公司向北辰示例贸易有限公司采购工业级碳酸钠，验收按照合同约定的质量指标执行。',
    '联系人沈柏宁负责核对发货清单，周示例负责到货检验，双方应保留完整的抽样和复检记录。',
    '本批货物共计五百吨，合同金额为人民币一百二十万元，付款后十个工作日内安排交付。',
    '卖方应当提供产品合格证和检测报告；发现质量不合格时，买方可以要求更换或退货。',
    '包装应适合长途运输和防潮存储，运输途中发生的破损由承担运输义务的一方负责处理。',
    '双方对本合同的解释发生争议时，应首先协商解决；协商不成的，依法向有管辖权的法院起诉。',
    '未经另一方书面同意，不得向第三方披露价格、客户名单和生产工艺，但依法披露的除外。',
    '验收不免除供货方对隐蔽质量缺陷的责任，索赔通知应说明缺陷、批号和受影响的数量。',
]
source = '\n'.join(clauses)
text = (source * (10000 // len(source) + 1))[:10000]
question = (source * 2)[:500]
nodes = [NodeIn(f'F1-P{i+1}', text[i:i+200]) for i in range(0, len(text), 200)]
records = json.loads(Path('/corpus.json').read_text())['records']
frozen_source = '\n'.join(r['input'] for r in records if r['origin'] == 'narrative')
frozen_text = (frozen_source * (10000 // len(frozen_source) + 1))[:10000]

def measure(label):
    rows = []
    for _ in range(3):
        start = time.perf_counter()
        if mode == 'body':
            ner.recognize(frozen_text)
            ner_seconds = time.perf_counter() - start
            start = time.perf_counter()
            compute(nodes, 'other', style='neutral', ratio=False)
            rows.append({'ner_seconds': ner_seconds, 'full_redaction_seconds': time.perf_counter() - start})
        else:
            _redact(question, {}, [])
            rows.append({'question_seconds': time.perf_counter() - start})
    return {'label': label, 'body_chars': len(text), 'question_chars': len(question),
            'mode': mode, 'threads': ner._threads, 'rows': rows,
            'frozen_ner_sha256': hashlib.sha256(frozen_text.encode()).hexdigest(),
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}

if mode == 'body':
    ner.recognize(frozen_text)
    compute(nodes[:1], 'other', style='neutral', ratio=False)
else:
    _redact(question, {}, [])
if '--once' in sys.argv:
    print(json.dumps(measure('local-script-check')), flush=True)
    raise SystemExit(0)
(root / (mode + '-ready')).write_text('ready')
while not (root / 'probe-stop').exists():
    request = root / ('probe-request-' + mode + '.json')
    if not request.exists():
        time.sleep(.2)
        continue
    job = json.loads(request.read_text())
    request.unlink()
    try:
        result = measure(job['label'])
    except Exception as exc:
        result = {'label': job['label'], 'error': type(exc).__name__}
    target = root / (job['label'] + '-' + mode + '.json')
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(result))
    tmp.replace(target)
