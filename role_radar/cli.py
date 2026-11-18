import argparse
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .crawler import discover_job_links
from .evaluator import evaluate_job
from .excel_store import ExcelStore
from .global_store import GlobalSearchStore
from .models import SearchConstraints
from .global_search import run_global_search


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="role-radar",
        description="Find relevant openings from a company career page.",
    )
    parser.add_argument("url", help="Career page or jobs listing URL")
    parser.add_argument(
        "--experience",
        default="0-1",
        help="Experience constraint, for example 0-1, 0-2, fresher, or '0-1, fresher'",
    )
    parser.add_argument(
        "--tech",
        action="append",
        default=[],
        help="Desired technology (repeatable), for example --tech Python --tech Django",
    )
    parser.add_argument(
        "--company",
        help="Company name override",
    )
    parser.add_argument(
        "--max-links",
        type=int,
        default=500,
        help="Maximum JD links to inspect (default: 500)",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("results"),
        help="Results directory (default: results)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_unknown",
        help="Include unknown or weak matches instead of only likely matches",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Discover job links but do not inspect JDs",
    )
    return parser


def run_global_search_command(arguments: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="role-radar search",
        description="Search public job boards for openings across the internet.",
    )
    parser.add_argument(
        "--tech",
        action="append",
        default=[],
        help="Desired technology (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--experience",
        default="0-1",
        help="Experience constraint, for example 0-1, fresher, or 0-1,fresher",
    )
    parser.add_argument(
        "--location",
        default="India",
        help="Location constraint, for example India,remote or Bangalore",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Only consider openings found in the last N days (default: 7)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=500,
        help="Maximum public search candidates to inspect (default: 500)",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("results"),
        help="Results directory (default: results)",
    )
    parser.add_argument(
        "--company",
        help="Optional company-name override",
    )

    args = parser.parse_args(arguments)
    technologies = [
        item.strip()
        for value in args.tech
        for item in value.split(",")
        if item.strip()
    ]
    if not technologies:
        parser.error("Provide at least one --tech value")
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.max_results < 1:
        parser.error("--max-results must be at least 1")

    print(
        f"Searching public boards for {', '.join(technologies)} "
        f"| experience: {args.experience} | location: {args.location} "
        f"| past {args.days} day(s)"
    )
    openings, unresolved = run_global_search(
        technologies=technologies,
        experience=args.experience,
        location=args.location,
        days=args.days,
        limit=args.max_results,
    )

    store = GlobalSearchStore(args.results)
    added, output = store.append(openings, unresolved)
    print(f"\nMatched {len(openings)} opening(s); added {added} new row(s).")
    print(f"Manual review entries: {len(unresolved)}")
    print(f"Results: {output.resolve()}")


def main() -> None:
    if sys.argv[1:2] == ["search"]:
        run_global_search_command(sys.argv[2:])
        return

    args = build_parser().parse_args()
    technologies = [
        item.strip()
        for value in args.tech
        for item in value.split(",")
        if item.strip()
    ]
    constraints = SearchConstraints(
        experience=args.experience,
        technologies=technologies,
        include_unknown=args.include_unknown,
    )
    links = discover_job_links(args.url, args.max_links)

    if args.list_only:
        print(f"Discovered {len(links)} job links:")
        for link in links:
            print(link)
        return

    openings = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(evaluate_job, link, args.company, constraints): index
            for index, link in enumerate(links, start=1)
        }
        for completed in as_completed(futures):
            index = futures[completed]
            print(f"Inspected JD {index}/{len(links)}")
            try:
                opening = completed.result()
            except Exception:
                opening = None
            if opening and opening.is_match:
                openings.append(opening)

    store = ExcelStore(args.results)
    added = store.append(openings)
    print(f"\nMatched {len(openings)} opening(s); added {added} new row(s).")
    print(f"Results: {store.results_dir.resolve()}")
