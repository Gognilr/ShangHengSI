"""U5-04：断网候选评测；仅 HTTP 回环调用同容器内的 llama.cpp。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

ROOT = Path('/evidence')
SAMPLES = [
    '买方青岚虚构化工有限公司向卖方北辰示例贸易有限公司采购工业级碳酸钠五百吨，合同总价人民币一百二十万元。卖方应在收到预付款后十个工作日内发货，并随货提供检测报告。买方应在到货后五个工作日内完成数量和外观验收；隐蔽质量缺陷不受该期限限制。未经双方书面同意，任何一方不得将合同权利义务转让给第三方。',
    '关于第三季度报价的回复：感谢贵方提供最新报价。我们原则上同意每吨二千四百元的价格，但请确认该价格是否包含增值税、包装费和运费。付款条件建议调整为预付百分之三十、到货验收后支付余款。若九月十五日前无法交付，请至少提前三个工作日书面告知，并提出分批交货方案。',
    '会议决定：由沈柏宁在下周一之前汇总检验数据，周示例在下周三完成供应商复评。当前批次的氯离子含量接近控制上限，尚不能据此认定不合格，应按约定方法复检。若复检结果超标，应隔离该批次并暂停使用；如复检合格，则经质量负责人批准后恢复发货。此纪要仅用于确认讨论事项，不变更合同约定。',
]
PARAGRAPHS = []
for i in range(80):
    PARAGRAPHS.append(f'第{i+1}条：' + SAMPLES[i % 3])
full = '\n'.join(PARAGRAPHS)[:10000]
chunks = full.splitlines()
(ROOT / 'samples.json').write_text(json.dumps({'samples': SAMPLES, 'full': full}, ensure_ascii=False))

def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    tmp.replace(path)

def probe(label):
    cached = ROOT / (label + '-mainline.json')
    if cached.exists():
        return json.loads(cached.read_text())
    results = {}
    for mode in ('body', 'question'):
        atomic(ROOT / ('probe-request-' + mode + '.json'), {'label': label})
        target = ROOT / (label + '-' + mode + '.json')
        deadline = time.monotonic() + 600
        while not target.exists():
            if time.monotonic() > deadline:
                raise TimeoutError('mainline probe')
            time.sleep(.5)
        results[mode] = json.loads(target.read_text())
    atomic(ROOT / (label + '-mainline.json'), results)
    return results

def translate(text, language):
    data = {'messages': [{'role': 'user', 'content':
            f'将以下文本翻译为{language}，注意只需要输出翻译后的结果，不要额外解释：\n{text}'}],
            'temperature': 0, 'max_tokens': 2048, 'stream': False, 'cache_prompt': False}
    req = urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions',
            data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1800) as response:
        result = json.load(response)
    if result['choices'][0]['finish_reason'] != 'stop':
        raise RuntimeError('incomplete translation')
    return result['choices'][0]['message']['content'], result.get('usage', {})

def concurrent_probe(label):
    if (ROOT / (label + '-mainline.json')).exists():
        return probe(label), True
    errors = []
    stop_load = threading.Event()
    def load():
        try:
            index = 0
            while not stop_load.is_set():
                translate(chunks[index % len(chunks)], '英语')
                index += 1
        except Exception as exc:
            errors.append(type(exc).__name__)
    worker = threading.Thread(target=load)
    worker.start()
    try:
        time.sleep(2)
        result = probe(label)
    finally:
        stop_load.set()
        worker.join()
    if errors:
        raise RuntimeError('translation load failed: ' + ','.join(errors))
    return result, False

def run():
    deadline = time.monotonic() + 600
    while not all((ROOT / (mode + '-ready')).exists() for mode in ('body', 'question')):
        if time.monotonic() > deadline:
            raise TimeoutError('mainline not ready')
        time.sleep(1)
    if not (ROOT / 'baseline-r3-mainline.json').exists():
        atomic(ROOT / 'status.json', {'phase': 'baseline'})
        probe('baseline-r3')
    configs = [('Hy-MT2-1.8B-Q8_0.gguf', '18b-q8'),
               ('HY-MT2-7B-Q8_0.gguf', '7b-q8'), ('HY-MT2-7B-Q6_K.gguf', '7b-q6')]
    for filename, name in configs:
        quality_path = ROOT / (f'r3-{name}-quality.json')
        # 恢复时沿用已完成的六份抽查，不重复模型调用。
        if not quality_path.exists():
            for previous_threads in (4, 6, 8):
                previous = ROOT / f'r3-{name}-t{previous_threads}.json'
                if previous.exists():
                    data = json.loads(previous.read_text())
                    if data.get('quality'):
                        atomic(quality_path, {'model': filename, 'threads': previous_threads,
                                              'quality': data['quality']})
                        break
        for threads in (4, 6, 8):
            label = f'r3-{name}-t{threads}'
            if (ROOT / (label + '.json')).exists():
                continue
            while not Path('/models', filename).exists():
                time.sleep(5)
            log = (ROOT / (label + '-server.log')).open('w')
            server = subprocess.Popen(['llama-server', '-m', '/models/' + filename,
                '-t', str(threads), '-tb', str(threads), '-c', '8192', '-np', '1',
                '-ngl', '0', '--host', '127.0.0.1', '--port', '8080',
                '--no-webui', '--no-warmup'], stdout=log, stderr=subprocess.STDOUT)
            start = time.monotonic()
            try:
                atomic(ROOT / 'status.json', {'phase': 'loading', 'label': label})
                while True:
                    if server.poll() is not None:
                        raise RuntimeError('server exited')
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2):
                            break
                    except Exception:
                        if time.monotonic() - start > 300:
                            raise TimeoutError('server load')
                        time.sleep(1)
                load_seconds = time.monotonic() - start
                # 预热独立于万字计时；并发探针在实际译文生成期间执行。
                warmup_start = time.monotonic()
                translate('供货方应当按照合同约定交付货物。', '英语')
                warmup_seconds = time.monotonic() - warmup_start
                atomic(ROOT / 'status.json', {'phase': 'concurrent', 'label': label})
                # 并发探针与万字单独运行分别计时，避免将混合耗时当作单跑速度。
                gate, probe_reused = concurrent_probe(label)
                results = [gate]
                failures = []
                if max(row['ner_seconds'] for row in gate['body']['rows']) > 15:
                    failures.append('NER_OVER_15_SECONDS')
                if max(row['question_seconds'] for row in gate['question']['rows']) > 3:
                    failures.append('QUESTION_OVER_3_SECONDS')
                # 用户 2026-11-04 同意：已不达标的配置不再跑万字长测，仍做质量抽查。
                skipped = bool(failures and (ROOT / 'skip-failed-long').exists())
                quality = []
                if not quality_path.exists():
                    # 质量先看，避免一小时长跑结束后才发现重大错译。
                    atomic(ROOT / 'status.json', {'phase': 'quality', 'label': label})
                    for language in ('英语', '日语'):
                        for index, sample in enumerate(SAMPLES):
                            output, usage = translate(sample, language)
                            quality.append({'language': language, 'sample': index,
                                            'translation': output, 'usage': usage})
                            atomic(ROOT / (f'r3-{name}-quality-progress.json'),
                                   {'model': filename, 'threads': threads, 'quality': quality})
                    atomic(quality_path, {'model': filename, 'threads': threads, 'quality': quality})
                rows = []
                seconds = None
                trace_name = None
                if not skipped:
                    start = time.monotonic()
                    atomic(ROOT / 'status.json', {'phase': 'translation', 'label': label})
                    trace_name = label + '-rows.jsonl'
                    trace = ROOT / trace_name
                    if trace.exists():
                        trace.rename(ROOT / (label + f'-rows-interrupted-{time.time_ns()}.jsonl'))
                    with trace.open('w') as sink:
                        for index, paragraph in enumerate(chunks):
                            output, usage = translate(paragraph, '英语')
                            row = {'source': paragraph, 'translation': output, 'usage': usage}
                            rows.append(row)
                            sink.write(json.dumps(row, ensure_ascii=False) + '\n')
                            sink.flush()
                            atomic(ROOT / (label + '-progress.json'), {'completed': index + 1,
                                'total': len(chunks), 'elapsed_seconds': time.monotonic() - start})
                    seconds = time.monotonic() - start
                status = Path(f'/proc/{server.pid}/status').read_text().splitlines()
                rss_peak = next(int(line.split()[1]) * 1024 for line in status if line.startswith('VmHWM:'))
                atomic(ROOT / (label + '.json'), {'model': filename, 'threads': threads,
                    'chars': 0 if skipped else len(full), 'planned_chars': len(full),
                    'load_seconds': load_seconds, 'warmup_seconds': warmup_seconds, 'seconds': seconds,
                    'long_skipped': skipped, 'gate_failures': failures, 'probe_reused': probe_reused,
                    'rows': rows, 'row_trace': trace_name, 'quality': quality,
                    'quality_threads': json.loads(quality_path.read_text())['threads'],
                    'mainline': results,
                    'server_peak_rss_bytes': rss_peak,
                    'container_cumulative_peak_bytes': int(Path('/sys/fs/cgroup/memory.peak').read_text())})
            finally:
                server.terminate()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                log.close()
    (ROOT / 'probe-stop').write_text('done')
    (ROOT / 'completed').write_text('done')
    atomic(ROOT / 'status.json', {'phase': 'completed'})

if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        atomic(ROOT / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
