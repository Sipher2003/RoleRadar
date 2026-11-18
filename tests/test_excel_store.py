from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from role_radar.excel_store import ExcelStore
from role_radar.models import Opening


def make_opening(link: str) -> Opening:
    return Opening(
        company="Example",
        title="Engineer",
        experience="0-1 years",
        posting_date=date(2026, 11, 18),
        link=link,
        is_match=True,
        confidence=80,
        reason="test",
    )


def test_append_and_deduplicate(tmp_path: Path):
    store = ExcelStore(tmp_path)
    assert store.append([make_opening("https://example.com/jobs/1")]) == 1
    assert store.append([make_opening("https://example.com/jobs/1")]) == 0

    workbook = load_workbook(tmp_path / "openings_001.xlsx")
    assert workbook.active.max_row == 2


def test_rollover_at_capacity(tmp_path: Path):
    store = ExcelStore(tmp_path, capacity=2)
    openings = [
        make_opening("https://example.com/jobs/1"),
        make_opening("https://example.com/jobs/2"),
        make_opening("https://example.com/jobs/3"),
    ]

    assert store.append(openings) == 3
    assert (tmp_path / "openings_001.xlsx").exists()
    assert (tmp_path / "openings_002.xlsx").exists()
