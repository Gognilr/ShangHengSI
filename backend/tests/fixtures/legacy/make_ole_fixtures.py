"""生成上传检查用的最小 OLE 复合文档样本（设计 docs/design/R2.md 第二节、第五节）。只用标准库。

olefile 只能读不能写，这里按 [MS-CFB] 第 3 版（512 字节扇区）手工构造：只有目录结构，流内容是填充字节，
不是能打开的 Word / Excel 文件，只供上传检查（文件头、目录中的存储与流名称）使用。
为省去 mini stream，每个流都补足到 4096 字节（mini stream 的分界），全部放在普通扇区。

在仓库根目录执行（结果确定，可重复生成）：

    python backend/tests/fixtures/legacy/make_ole_fixtures.py
"""
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent

SECTOR = 512
MIN_STREAM = 4096
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF
MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# 存储用 dict 表示，流用 bytes 表示
SAMPLES = {
    # Word 宏：Macros 存储（其下 VBA 存储）
    "macro_word.doc": {
        "WordDocument": b"fictional", "1Table": b"",
        "Macros": {"VBA": {"dir": b"", "_VBA_PROJECT": b""}, "PROJECT": b""},
    },
    # Excel 宏：_VBA_PROJECT_CUR 存储
    "macro_excel.xls": {
        "Workbook": b"fictional",
        "_VBA_PROJECT_CUR": {"VBA": {"dir": b"", "_VBA_PROJECT": b""}, "PROJECT": b""},
    },
    # 加了打开密码的 DOCX：OLE 中的 EncryptionInfo、EncryptedPackage
    "encrypted_ooxml.docx": {
        "EncryptionInfo": b"\x04\x00\x04\x00\x40\x00\x00\x00", "EncryptedPackage": b"fictional",
        "\x06DataSpaces": {"Version": b""},
    },
    # Excel 5（BIFF5）的主数据流名为 Book
    "excel5_book.xls": {"Book": b"fictional"},
    # 合法 OLE，但没有 WordDocument 流
    "word_no_main.doc": {"1Table": b"", "Data": b""},
}


def _sort_key(name: str) -> tuple[int, str]:
    # 同一存储内兄弟节点的比较规则：先比名称长度，再比大写后的名称（MS-CFB 2.6.4）
    return len(name), name.upper()


def build(tree: dict) -> bytes:
    entries = []  # [name, type, child, right, data]

    def add(name, node) -> int:
        idx = len(entries)
        if isinstance(node, dict):
            entries.append([name, 5 if idx == 0 else 1, NOSTREAM, NOSTREAM, None])
            kids = [add(k, node[k]) for k in sorted(node, key=_sort_key)]
            # 兄弟节点按顺序串成右链：是合法的二叉查找树（全黑、不平衡，读取方不校验平衡）
            for a, b in zip(kids, kids[1:]):
                entries[a][3] = b
            if kids:
                entries[idx][2] = kids[0]
        else:
            entries.append([name, 2, NOSTREAM, NOSTREAM, node.ljust(MIN_STREAM, b"\x00")])
        return idx

    add("Root Entry", tree)

    sectors: list[bytes] = []
    fat: list[int] = []
    starts = {}
    for i, e in enumerate(entries):
        data = e[4]
        if data is None:
            continue
        n = (len(data) + SECTOR - 1) // SECTOR
        starts[i] = len(sectors)
        for k in range(n):
            sectors.append(data[k * SECTOR:(k + 1) * SECTOR].ljust(SECTOR, b"\x00"))
            fat.append(len(sectors) if k < n - 1 else ENDOFCHAIN)

    dir_bytes = b""
    for i, (name, typ, child, right, data) in enumerate(entries):
        raw = (name + "\x00").encode("utf-16-le")
        start = starts.get(i, ENDOFCHAIN if typ == 5 else 0)
        size = len(data) if data is not None else 0
        dir_bytes += (
            raw.ljust(64, b"\x00") + struct.pack("<HBB3I", len(raw), typ, 1, NOSTREAM, right, child)
            + b"\x00" * 16 + struct.pack("<I", 0) + b"\x00" * 16 + struct.pack("<IQ", start, size)
        )
    empty = b"\x00" * 64 + struct.pack("<HBB3I", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM) + b"\x00" * 36 + struct.pack("<IQ", 0, 0)
    while len(dir_bytes) % SECTOR:
        dir_bytes += empty
    first_dir = len(sectors)
    n_dir = len(dir_bytes) // SECTOR
    for k in range(n_dir):
        sectors.append(dir_bytes[k * SECTOR:(k + 1) * SECTOR])
        fat.append(len(sectors) if k < n_dir - 1 else ENDOFCHAIN)

    n_fat = 1
    while (len(sectors) + n_fat) > n_fat * (SECTOR // 4):
        n_fat += 1
    fat_ids = list(range(len(sectors), len(sectors) + n_fat))
    fat += [FATSECT] * n_fat
    fat += [FREESECT] * (n_fat * (SECTOR // 4) - len(fat))
    fat_bytes = struct.pack(f"<{len(fat)}I", *fat)
    for k in range(n_fat):
        sectors.append(fat_bytes[k * SECTOR:(k + 1) * SECTOR])

    difat = fat_ids + [FREESECT] * (109 - n_fat)
    header = (
        MAGIC + b"\x00" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
        + struct.pack("<IIIIIIIII", 0, n_fat, first_dir, 0, MIN_STREAM, ENDOFCHAIN, 0, ENDOFCHAIN, 0)
        + struct.pack("<109I", *difat)
    )
    assert len(header) == SECTOR
    return header + b"".join(sectors)


def main() -> None:
    for name, tree in SAMPLES.items():
        (HERE / name).write_bytes(build(tree))
        print(name)


if __name__ == "__main__":
    main()
