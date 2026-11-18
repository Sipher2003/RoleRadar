from role_radar.evaluator import extract_experience, extract_title
from role_radar.models import SearchConstraints
from role_radar.evaluator import evaluate_job
from bs4 import BeautifulSoup


def test_extract_experience_range():
    assert extract_experience("Candidates should have 0-2 years of experience") == "0-2 years"


def test_extract_title():
    soup = BeautifulSoup("<html><h1>Associate Software Engineer</h1></html>", "html.parser")
    assert extract_title(soup) == "Associate Software Engineer"


def test_constraints_range():
    assert SearchConstraints(experience="0-1").experience_range() == (0.0, 1.0)
    assert SearchConstraints(experience="fresher").experience_range() == (0.0, 1.0)
    assert SearchConstraints(experience="0-1, fresher").experience_range() == (0.0, 1.0)


def test_technologies_use_or_semantics(monkeypatch):
    pages = {
        "https://example.com/job/python/100001/": "<html><h1>Engineer</h1><p>Python, SQL, 0-1 years of experience</p></html>",
        "https://example.com/job/java/100002/": "<html><h1>Engineer</h1><p>Java, SQL, 0-1 years of experience</p></html>",
        "https://example.com/job/go/100003/": "<html><h1>Engineer</h1><p>Go, SQL, 0-1 years of experience</p></html>",
    }

    def fake_fetch(link):
        return pages[link], link

    monkeypatch.setattr("role_radar.evaluator.fetch", fake_fetch)
    constraints = SearchConstraints(experience="0-1", technologies=["Python", "Java"])

    assert evaluate_job("https://example.com/job/python/100001/", "Example", constraints).is_match
    assert evaluate_job("https://example.com/job/java/100002/", "Example", constraints).is_match
    assert not evaluate_job("https://example.com/job/go/100003/", "Example", constraints).is_match


def test_listing_and_senior_jobs_are_rejected(monkeypatch):
    pages = {
        "https://example.com/go/Deloitte/718244/?q=&sortDirection=desc":
            "<html><h1>Deloitte India</h1><p>Search and sort jobs</p></html>",
        "https://example.com/job/Lead-Manager/123456/":
            "<html><h1>Lead Engineering Manager</h1><p>Python. 8-10 years experience required.</p></html>",
    }

    def fake_fetch(link):
        return pages[link], link

    monkeypatch.setattr("role_radar.evaluator.fetch", fake_fetch)
    constraints = SearchConstraints(experience="0-1", technologies=["Python"])

    assert evaluate_job(
        "https://example.com/go/Deloitte/718244/?q=&sortDirection=desc",
        "Example",
        constraints,
    ) is None
    assert not evaluate_job(
        "https://example.com/job/Lead-Manager/123456/",
        "Example",
        constraints,
    ).is_match
