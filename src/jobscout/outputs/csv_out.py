"""CSV export of tracked jobs."""

import csv
import os
from pathlib import Path

from jobscout.models import STATUS_LABELS, JobRow

CSV_COLUMNS = [
    "Fit score", "Title", "Company", "Location", "Status", "Reason", "Flags", "Starts", "Deadline",
    "Posted", "Source", "Job URL", "Recruiter", "Recruiter title", "Recruiter contact", "Keyword", "Job ID",
]  # fmt: skip


def _safe(text: str) -> str:
    """Neutralise spreadsheet formulas: scraped text is untrusted and Excel runs cells starting with = + - @."""
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _record(row: JobRow) -> list[str]:
    score = "" if row.score is None else str(row.score)
    return [score] + [_safe(cell) for cell in _text_cells(row)]


def _text_cells(row: JobRow) -> list[str]:
    return [
        row.title,
        row.company,
        row.location,
        STATUS_LABELS[row.status],
        row.reason,
        "; ".join(row.flags),
        row.start_date,
        row.deadline,
        row.posted,
        row.source,
        row.url,
        row.recruiter,
        row.recruiter_title,
        row.recruiter_url,
        row.keyword,
        row.pk,
    ]


def export_csv(rows: list[JobRow], path: Path) -> int:
    """Write the jobs, best score first, as UTF-8 with a BOM so Excel opens it cleanly."""
    ordered = sorted(rows, key=lambda r: -1 if r.score is None else r.score, reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(_record(r) for r in ordered)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(ordered)
