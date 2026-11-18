from pathlib import Path
import re

from openpyxl import Workbook, load_workbook

from .models import Opening


HEADERS = [
    "Company",
    "Title",
    "Experience asked",
    "Date of posting",
    "Link to apply",
]


class ExcelStore:
    def __init__(self, results_dir: Path, capacity: int = 50) -> None:
        self.results_dir = results_dir
        self.capacity = capacity
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def append(self, openings: list[Opening]) -> int:
        if not openings:
            return 0

        workbooks = sorted(
            self.results_dir.glob("openings_*.xlsx"),
            key=lambda path: self._workbook_number(path),
        )
        target = workbooks[-1] if workbooks else None
        if target is None:
            target = self.results_dir / "openings_001.xlsx"
            number = 1
        else:
            number = self._workbook_number(target)

        if target.exists() and self._row_count(target) >= self.capacity:
            number = self._workbook_number(target) + 1
            target = self.results_dir / f"openings_{number:03d}.xlsx"

        added = 0
        pending = list(openings)
        while pending:
            added += self._append_to_new_or_existing(target, pending)
            if self._row_count(target) < self.capacity:
                break
            known_links = self._known_links(target)
            pending = [
                opening for opening in pending
                if opening.link not in known_links
            ]
            number += 1
            target = self.results_dir / f"openings_{number:03d}.xlsx"
            if not pending:
                break
        return added

    def _append_to_new_or_existing(self, target: Path, openings: list[Opening]) -> int:
        if target.exists():
            workbook = load_workbook(target)
            sheet = workbook.active
            available = max(self.capacity - self._row_count_from_sheet(sheet), 0)
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Openings"
            sheet.append(HEADERS)
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = f"A1:E1"
            available = self.capacity

        known_links = {str(row[4]).strip() for row in sheet.iter_rows(min_row=2, values_only=True)}
        added = 0
        for opening in openings:
            if added >= available:
                break
            if not opening.link or opening.link in known_links:
                continue
            sheet.append([
                opening.company,
                opening.title,
                opening.experience,
                opening.posting_date.isoformat() if opening.posting_date else "Unknown",
                opening.link,
            ])
            link_cell = sheet.cell(row=sheet.max_row, column=5)
            link_cell.hyperlink = opening.link
            link_cell.style = "Hyperlink"
            known_links.add(opening.link)
            added += 1

        if added:
            workbook.save(target)
        elif not target.exists():
            workbook.save(target)
        return added

    def _row_count(self, path: Path) -> int:
        workbook = load_workbook(path, read_only=True)
        count = workbook.active.max_row - 1
        workbook.close()
        return max(count, 0)

    def _known_links(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        workbook = load_workbook(path, read_only=True)
        links = {
            str(row[4]).strip()
            for row in workbook.active.iter_rows(min_row=2, values_only=True)
            if len(row) > 4 and row[4]
        }
        workbook.close()
        return links

    @staticmethod
    def _row_count_from_sheet(sheet) -> int:
        return max(sheet.max_row - 1, 0)

    @staticmethod
    def _workbook_number(path: Path) -> int:
        match = re.search(r"(\d+)$", path.stem)
        return int(match.group(1)) if match else 0
