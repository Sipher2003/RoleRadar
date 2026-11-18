import argparse
from pathlib import Path

from .crawler import discover_job_links
from .evaluator import evaluate_job
from .excel_store import ExcelStore
from .models import SearchConstraints


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


def main() -> None:
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
    for index, link in enumerate(links, start=1):
        print(f"Inspecting JD {index}/{len(links)}: {link}")
        opening = evaluate_job(link, args.company, constraints)
        if opening and opening.is_match:
            openings.append(opening)

    store = ExcelStore(args.results)
    added = store.append(openings)
    print(f"\nMatched {len(openings)} opening(s); added {added} new row(s).")
    print(f"Results: {store.results_dir.resolve()}")
