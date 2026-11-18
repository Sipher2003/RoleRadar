from datetime import date

from openpyxl import load_workbook

from role_radar.global_store import GlobalSearchStore
from role_radar.models import Opening


def test_global_store_creates_manual_review_sheet(tmp_path):
    opening = Opening(
        company="Example",
        title="Software Engineer",
        experience="0-1 years",
        posting_date=date(2026, 11, 18),
        link="https://example.com/job/software-engineer",
        is_match=True,
        confidence=80,
        reason="test",
        location="Remote",
    )
    unresolved = [{
        "title": "Unknown board result",
        "link": "https://example.com/jobs/search",
        "reason": "Could not extract a verified job-detail page",
    }]

    added, target = GlobalSearchStore(tmp_path).append([opening], unresolved)
    assert added == 1
    workbook = load_workbook(target)
    assert "Openings" in workbook.sheetnames
    assert "Manual Review" in workbook.sheetnames
    assert workbook["Openings"]["G2"].hyperlink.target == opening.link
    assert workbook["Manual Review"]["B2"].hyperlink.target == unresolved[0]["link"]


def test_global_store_purges_non_job_manual_entries(tmp_path):
    store = GlobalSearchStore(tmp_path)
    opening = Opening(
        company="Example",
        title="Engineer",
        experience="0-1 years",
        posting_date=None,
        link="https://example.com/job/engineer",
        is_match=True,
        confidence=80,
        reason="test",
        location="India",
    )
    _, target = store.append([opening], [])

    workbook = load_workbook(target)
    manual = workbook["Manual Review"]
    manual.append(["Python Tutorial", "https://www.python.org/", "Old stale entry"])
    manual.append(["Python Jobs", "https://www.linkedin.com/jobs/view/123", "Manual check"])
    workbook.save(target)

    store.append([], [])
    workbook = load_workbook(target)
    manual = workbook["Manual Review"]
    links = [manual.cell(row=row, column=2).value for row in range(2, manual.max_row + 1)]
    assert "https://www.python.org/" not in links
