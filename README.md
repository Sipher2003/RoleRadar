# Role Radar

Role Radar scans a company career page, discovers job-detail links, reads each JD, and exports genuine likely matches to Excel accordint to the user preferences.

## Setup

```powershell
py -m pip install -e .
py -m playwright install chromium
```

## Basic run

```powershell
role-radar "https://company.example/careers" --experience "0-1" --tech Python --tech Django
```

Options:

- Repeat `--tech` or separate technologies with commas.
- Use `--experience "fresher"`, `--experience "new grad"`, or `--experience "0-1, fresher"`.
- Use `--company "Company Name"` if the domain does not identify the employer.
- Use `--all` to include weak matches.
- Use `--list-only` to check discovered URLs without reading JDs.
- Use `--results D:\results` to choose another output folder.

## Output

Excel files are written to `results/openings_001.xlsx`, `openings_002.xlsx`, and so on. Each file has at most 50 openings. Existing links are deduplicated, and future runs append to the latest file until it reaches 50.

The evaluator deliberately reads the JD. It checks:

- an explicit or stated experience floor,
- technologies mentioned in the JD,
- entry-level/fresh-graduate language,
- absence of a rigid experience requirement.

It does not treat a title containing "senior" as a title-only exclusion: if the JD itself fits the constraints, it can still be reported.
