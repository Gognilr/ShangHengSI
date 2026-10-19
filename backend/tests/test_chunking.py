"""分段（M5-01，SPEC 11.5，设计 docs/design/M5.md 第二节）：纯函数测试。"""
from app.review.chunking import MIN_LIMIT, OutLine, effective_limit, outbound_lines, split_chunks


def line(loc: str, size: int, boundary: bool = False) -> OutLine:
    prefix = f"[{loc}] "
    return OutLine(loc, prefix + "字" * (size - len(prefix) - 1), boundary)  # 含换行共 size 个字符


def locs(chunks) -> list[list[str]]:
    return [[x.split("]")[0][1:] for x in c.lines] for c in chunks]


def test_outbound_lines_parses_locs_and_boundaries():
    body = "[F1-P1] 销售合同\n[F1-P2] 第1条 货物\n[F1-T1] 【报价明细表：共 3 行】\n\n[F2-P1] 附件"
    lines = outbound_lines(body, {"F1-P1", "F1-P2", "F2-P1"})
    assert [(x.loc, x.boundary) for x in lines] == [
        ("F1-P1", True), ("F1-P2", True), ("F1-T1", False), ("F2-P1", True)]
    assert lines[2].text == "[F1-T1] 【报价明细表：共 3 行】"


def test_single_chunk_when_within_limit_or_empty():
    lines = [line("F1-P1", 400, True), line("F1-P2", 600)]
    (c,) = split_chunks(lines, 1000)
    assert (c.index, c.total, c.first_loc, c.last_loc) == (1, 1, "F1-P1", "F1-P2")
    assert c.locs == {"F1-P1", "F1-P2"} and c.lines == [x.text for x in lines]
    (empty,) = split_chunks([], 1000)
    assert (empty.total, empty.lines, empty.first_loc) == (1, [], "")


def test_cuts_at_boundaries_greedily():
    lines = [line("F1-P1", 300, True), line("F1-P2", 300), line("F1-P3", 300, True), line("F1-P4", 300),
             line("F1-P5", 300, True), line("F1-P6", 100)]
    chunks = split_chunks(lines, 1000)
    # 单元：P1-P2(600)、P3-P4(600)、P5-P6(400)；600+600 放不下，600+400 放得下
    assert locs(chunks) == [["F1-P1", "F1-P2"], ["F1-P3", "F1-P4", "F1-P5", "F1-P6"]]
    assert [(c.index, c.total) for c in chunks] == [(1, 2), (2, 2)]
    assert all(sum(len(x) + 1 for x in c.lines) <= 1000 for c in chunks)


def test_oversized_unit_split_by_lines_and_oversized_line_alone():
    lines = [line("F1-P1", 400, True), line("F1-P2", 400), line("F1-P3", 400), line("F1-P4", 1500),
             line("F1-P5", 200)]
    chunks = split_chunks(lines, 1000)
    assert locs(chunks) == [["F1-P1", "F1-P2"], ["F1-P3"], ["F1-P4"], ["F1-P5"]]
    assert chunks[2].first_loc == chunks[2].last_loc == "F1-P4"


def test_every_line_kept_once_in_order():
    lines = [line(f"F1-P{i}", 120 + (i * 37) % 300, i % 4 == 0) for i in range(1, 80)]
    chunks = split_chunks(lines, 1000)
    assert [x for c in chunks for x in c.lines] == [x.text for x in lines]
    assert len(chunks) > 5 and all(c.total == len(chunks) for c in chunks)


def test_effective_limit():
    assert effective_limit(None) == 60000 and effective_limit("abc") == 60000 and effective_limit(0) == 60000
    assert effective_limit(10) == MIN_LIMIT == 1000 and effective_limit(5000) == 5000
