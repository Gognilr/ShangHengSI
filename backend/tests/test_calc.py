"""代号计算的代表用例；不使用模型，不把结果写回文本。"""
from app.calc import evaluate


def test_gross_margin_and_variants():
    mapping = {'[金额_1]': 'CNY 1234.50', '[金额_2]': '人民币 800.00元'}
    text = '收入[金额_1]。\n【算式】毛利率 = （【Amount_1】 − [金额_2]） ÷ [金额_1] × 100%\n'
    row, = evaluate(text, mapping, labels={'[金额_1]': '营业收入（F1-S1-B2）'})
    assert row['status'] == 'ok' and row['result'] == '35.2%'
    assert row['line_start'] == len('收入CNY 1234.50。\n')
    assert '营业收入（F1-S1-B2）CNY 1234.50' in row['expression']
    assert evaluate('【算式】差额 = ［[AMT_1]］ - [[AMT_2]]',
                    {'[[AMT_1]]': '0.30', '[[AMT_2]]': '0.10'})[0]['result'] == '0.20'
    assert evaluate('【算式】合计 = [金额_1] + 1', {'[金额_1]': '人民币壹佰元整'})[0]['result'] == '101.00 CNY'


def test_unknown_code():
    row, = evaluate('【算式】合计 = [金额_9] + 1', {'[金额_1]': '2'})
    assert row['status'] == 'unknown_code' and row['result'] == ''
    assert evaluate('【算式】合计 = 金额1 + 1', {'[金额_1]': '2'})[0]['status'] == 'unknown_code'


def test_mixed_currency():
    row, = evaluate('【算式】合计 = [金额_1] + [单价_2]', {'[金额_1]': 'USD 2', '[单价_2]': 'CNY 3'})
    assert row['status'] == 'currency_mixed' and row['result'] == ''


def test_zero_divisor_and_unsupported():
    mapping = {'[[AMT_1]]': '3'}
    assert evaluate('【算式】比率 = [[AMT_1]] ÷ (1-1)', mapping)[0]['status'] == 'div_zero'
    for expr in ('__import__("os")', '1**2', '1//2', '2e3', '[[AMT_1]] +', '3,000', '1 2', '[[AMT_1]] + v0'):
        row, = evaluate('【算式】合计 = ' + expr, mapping)
        assert row['status'] != 'ok' and row['result'] == ''
