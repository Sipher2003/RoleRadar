from dataclasses import dataclass
from datetime import date
from typing import Optional
import re


@dataclass
class SearchConstraints:
    experience: str = "0-1"
    technologies: list[str] | None = None
    include_unknown: bool = False
    location: str = "India"

    def __post_init__(self) -> None:
        self.technologies = [item.strip() for item in (self.technologies or []) if item.strip()]

    def experience_range(self) -> tuple[float, float]:
        normalized = self.experience.lower().strip()
        ranges = re.findall(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", normalized)
        if ranges:
            return min(float(low) for low, _ in ranges), max(float(high) for _, high in ranges)
        if any(term in normalized for term in ("fresher", "fresh graduate", "new grad", "graduate", "entry level", "entry-level")):
            return 0.0, 1.0
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", self.experience)
        if match:
            return float(match.group(1)), float(match.group(2))
        match = re.search(r"\d+(?:\.\d+)?", self.experience)
        if match:
            value = float(match.group())
            return value, value
        return 0.0, 1.0


@dataclass
class Opening:
    company: str
    title: str
    experience: str
    posting_date: Optional[date]
    link: str
    is_match: bool
    confidence: int
    reason: str
    location: str = "Unknown"
    source: str = "Unknown"
