from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass

from playwright.sync_api import Frame, Page


FUBON_HEADER_WORDS = (
    "交易日",
    "帳務日",
    "交易時間",
    "交易摘要",
    "摘要",
    "支出金額",
    "存入金額",
    "餘額",
    "備註",
)


@dataclass(frozen=True)
class CapturedTable:
    headers: list[str]
    rows: list[list[str]]

    @property
    def fingerprint(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        return tuple(self.headers), tuple(tuple(row) for row in self.rows)

    def to_tsv(self, include_headers: bool = True) -> str:
        source_rows = [self.headers, *self.rows] if include_headers else self.rows
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        for row in source_rows:
            writer.writerow([str(value).replace("\r", "").strip() for value in row])
        return output.getvalue().rstrip("\n")


def _matrix(table: object) -> list[list[str]]:
    return table.evaluate(
        """
        table => Array.from(table.rows).map(row =>
          Array.from(row.cells).map(cell => (cell.innerText || '').trim())
        )
        """
    )


def _header_score(row: list[str]) -> int:
    text = " ".join(row).replace(" ", "")
    return sum(word in text for word in FUBON_HEADER_WORDS)


def find_fubon_statement(page: Page) -> CapturedTable | None:
    """尋找目前畫面中的富邦交易明細表，不依賴容易變動的元素 ID。"""
    best: tuple[int, list[list[str]]] | None = None
    for context in [page, *page.frames]:
        try:
            candidate: Frame | Page = context
            tables = candidate.locator("table")
            for index in range(tables.count()):
                table = tables.nth(index)
                if not table.is_visible():
                    continue
                matrix = _matrix(table)
                if len(matrix) < 2:
                    continue
                score = max((_header_score(row) for row in matrix[:6]), default=0)
                if score >= 4 and (best is None or score > best[0]):
                    best = score, matrix
        except Exception:
            # 換頁期間 frame 可能被替換；下一輪重新掃描即可。
            continue

    if best is None:
        return None

    matrix = best[1]
    header_index = max(range(min(6, len(matrix))), key=lambda index: _header_score(matrix[index]))
    headers = [value.strip() or f"欄位{index + 1}" for index, value in enumerate(matrix[header_index])]
    rows: list[list[str]] = []
    for raw_row in matrix[header_index + 1 :]:
        row = [value.strip() for value in raw_row]
        joined = "".join(row).replace(" ", "")
        if not joined or "總計" in joined or len(row) != len(headers):
            continue
        rows.append(row)
    if not rows:
        return None
    return CapturedTable(headers=headers, rows=rows)


def copy_to_clipboard(table: CapturedTable, include_headers: bool = False) -> None:
    subprocess.run(["pbcopy"], input=table.to_tsv(include_headers=include_headers), text=True, check=True)
