from pathlib import Path
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from urllib.parse import urlparse

from .models import Opening


OPENING_HEADERS = [
    "Company",
    "Title",
    "Experience asked",
    "Location",
    "Source",
    "Date of posting",
    "Link to apply",
]


class GlobalSearchStore:
    def __init__(self, results_dir: Path, capacity: int = 50) -> None:
        self.results_dir = results_dir
        self.capacity = capacity
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        openings: list[Opening],
        unresolved: list[dict],
    ) -> tuple[int, Path]:
        workbooks = sorted(
            self.results_dir.glob("search_openings_*.xlsx"),
            key=lambda path: self._number(path),
        )
        target = workbooks[-1] if workbooks else self.results_dir / "search_openings_001.xlsx"
        if target.exists() and self._row_count(target, "Openings") >= self.capacity:
            target = self.results_dir / f"search_openings_{self._number(target) + 1:03d}.xlsx"

        if target.exists():
            workbook = load_workbook(target)
        else:
            workbook = Workbook()
            opening_sheet = workbook.active
            opening_sheet.title = "Openings"

        opening_sheet = workbook["Openings"] if "Openings" in workbook.sheetnames else workbook.active
        if opening_sheet.max_row == 1 and opening_sheet["A1"].value is None:
            for column, header in enumerate(OPENING_HEADERS, start=1):
                opening_sheet.cell(row=1, column=column, value=header)
            opening_sheet.freeze_panes = "A2"
            opening_sheet.auto_filter.ref = "A1:G1"

        known_links = {
            str(row[6]).strip()
            for row in opening_sheet.iter_rows(min_row=2, values_only=True)
            if len(row) > 6 and row[6]
        }
        added = 0
        for opening in openings:
            if opening_sheet.max_row - 1 >= self.capacity:
                break
            if not opening.link or opening.link in known_links:
                continue
            opening_sheet.append([
                opening.company,
                opening.title,
                opening.experience,
                getattr(opening, "location", "Unknown"),
                getattr(opening, "source", "Unknown"),
                opening.posting_date.isoformat() if opening.posting_date else "Unknown",
                opening.link,
            ])
            link_cell = opening_sheet.cell(row=opening_sheet.max_row, column=7)
            link_cell.hyperlink = opening.link
            link_cell.style = "Hyperlink"
            known_links.add(opening.link)
            added += 1

        if "Manual Review" in workbook.sheetnames:
            manual_sheet = workbook["Manual Review"]
        else:
            manual_sheet = workbook.create_sheet("Manual Review")
            for column, header in enumerate(
                ["Title", "Search result / source link", "Source", "Reason"],
                start=1,
            ):
                manual_sheet.cell(row=1, column=column, value=header)
            manual_sheet.freeze_panes = "A2"
            manual_sheet.auto_filter.ref = "A1:D1"

        header_fill = PatternFill("solid", fgColor="FFF2CC")
        header_font = Font(bold=True)
        for cell in manual_sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        for cell in opening_sheet[1]:
            cell.fill = header_fill
            cell.font = header_font

        non_job_hosts = (
            "python.org",
            "w3schools",
            "geeksforgeeks",
            "stackoverflow",
            "github.com",
            "youtube.com",
        )
        job_hosts = (
            "linkedin.com",
            "indeed.com",
            "naukri.com",
            "wellfound.com",
            "glassdoor.com",
            "workday",
            "greenhouse.io",
            "lever.co",
            "smartrecruiters",
            "ashbyhq",
        )
        for row_number in range(manual_sheet.max_row, 1, -1):
            title = str(manual_sheet.cell(row=row_number, column=1).value or "").lower()
            link = str(manual_sheet.cell(row=row_number, column=2).value or "").lower()
            host = urlparse(link).netloc.lower()
            path = urlparse(link).path.lower()
            job_path = any(marker in path for marker in (
                "/job/",
                "/jobs/",
                "/jobs/view/",
                "/viewjob",
                "/career",
                "/vacancy",
                "/opening",
            ))
            keep = (
                any(marker in host for marker in job_hosts)
                or job_path
            ) and not any(marker in host for marker in non_job_hosts)
            if not keep:
                manual_sheet.delete_rows(row_number, 1)

        manual_links = {
            str(row[1]).strip()
            for row in manual_sheet.iter_rows(min_row=2, values_only=True)
            if len(row) > 1 and row[1]
        }
        for item in unresolved:
            link = item["link"]
            if not link or link in manual_links:
                continue
            manual_sheet.append([
                item["title"],
                link,
                item.get("source", "Unknown"),
                item["reason"],
            ])
            link_cell = manual_sheet.cell(row=manual_sheet.max_row, column=2)
            link_cell.hyperlink = link
            link_cell.style = "Hyperlink"
            manual_links.add(link)

        for sheet in (opening_sheet, manual_sheet):
            sheet.sheet_view.showGridLines = False
            for column in sheet.columns:
                width = min(max(len(str(cell.value or "")) for cell in column) + 2, 70)
                sheet.column_dimensions[column[0].column_letter].width = width

        workbook.save(target)
        return added, target

    def _row_count(self, path: Path, sheet_name: str) -> int:
        workbook = load_workbook(path, read_only=True)
        if sheet_name not in workbook.sheetnames:
            workbook.close()
            return 0
        count = workbook[sheet_name].max_row - 1
        workbook.close()
        return max(count, 0)

    @staticmethod
    def _number(path: Path) -> int:
        match = re.search(r"(\d+)$", path.stem)
        return int(match.group(1)) if match else 0
