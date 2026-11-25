"""The "Course grades" example: a class roster and a gradebook export.

It is the example aimed at instructors in the audience. These tests need no model: they
check that the request loads, that the data is dirty in exactly the ways the request
describes, that a correct answer exists (a plain pandas reference solution), and that the
students are made up.
"""

import csv
import io
from pathlib import Path

import benchmarks.make_fixtures as fixtures
import pandas as pd

from adpb.requests_io import load_request_file

REPO = Path(__file__).resolve().parents[2]
REQUEST = REPO / "benchmarks" / "requests" / "course_grades.yaml"
DATA = REPO / "benchmarks" / "fixtures" / "grades"


def _read(name: str) -> pd.DataFrame:
    """Every value as text, skipping lines with the wrong number of fields - the same
    starting point the pipeline gives its agents."""
    rows = list(csv.reader(io.StringIO((DATA / name).read_text())))
    header = rows[0]
    return pd.DataFrame([r for r in rows[1:] if len(r) == len(header)], columns=header)


def reference_answer() -> pd.DataFrame:
    """What the request asks for, written by hand."""
    roster = _read("roster.csv")
    roster["student_id"] = roster["student_id"].str.strip()
    roster = roster[roster["student_id"].str.fullmatch(r"\d+")].drop_duplicates("student_id")
    roster = roster[roster["status"].str.strip().str.lower() == "enrolled"]
    scores = _read("scores.csv").drop_duplicates()
    scores["student_id"] = scores["student_id"].str.strip()
    points = scores["score"].str.replace("pts", "", regex=False).str.strip()
    scores["points"] = pd.to_numeric(points, errors="coerce")
    scores["max_points"] = pd.to_numeric(scores["max_points"], errors="coerce")
    scores = scores[scores["points"].notna() & (scores["points"] >= 0)]
    scores = scores[scores["student_id"].isin(roster["student_id"])]
    totals = scores.groupby("student_id").agg(assignments_submitted=("points", "size"),
                                              got=("points", "sum"),
                                              possible=("max_points", "sum")).reset_index()
    totals["average_percent"] = (totals["got"] / totals["possible"] * 100).round(1)
    return roster.merge(totals, on="student_id")[
        ["student_id", "full_name", "section", "assignments_submitted", "average_percent"]]


def test_request_loads_with_a_roster_and_a_gradebook():
    request, files = load_request_file(REQUEST)
    assert [s.name for s in request.sources] == ["roster", "scores"]
    assert all(f.exists() for f in files)
    assert request.target_table == "course_grades"
    assert "withdrawn" in request.request and "absent" in request.request


def test_data_is_dirty_in_the_ways_the_request_describes():
    roster, scores = _read("roster.csv"), _read("scores.csv")
    assert roster["student_id"].str.startswith(" ").any()            # stray spaces in ids
    assert roster.duplicated().any()                                  # a repeated roster row
    assert {"withdrawn", "Withdrawn", "WITHDRAWN"} & set(roster["status"].str.strip())
    assert roster["section"].str.strip().str.lower().nunique() < roster["section"].nunique()
    assert scores["score"].isin(["absent", "EX"]).any()               # not scores at all
    assert scores["score"].str.contains("pts").any()                  # a unit in the number
    assert scores["score"].str.strip().str.startswith("-").any()      # a negative score
    assert scores.duplicated().any()                                  # a repeated score line
    unknown = set(scores["student_id"].str.strip()) - set(roster["student_id"].str.strip())
    assert unknown                                                    # not on the roster
    raw = (DATA / "scores.csv").read_text().splitlines()
    assert any(line.count(",") != raw[0].count(",") for line in raw)  # a malformed line


def test_a_correct_answer_has_one_row_per_enrolled_student():
    answer = reference_answer()
    assert 50 <= len(answer) <= 60
    assert answer["student_id"].is_unique
    assert answer["average_percent"].between(0, 100).all()
    withdrawn = _read("roster.csv")
    withdrawn = withdrawn[withdrawn["status"].str.strip().str.lower() == "withdrawn"]
    assert not set(withdrawn["student_id"].str.strip()) & set(answer["student_id"])


def test_students_are_made_up():
    # No real student records: every address is on the reserved example.edu domain.
    emails = _read("roster.csv")["email"].str.strip().str.lower()
    assert emails.str.endswith("@example.edu").all()


def test_data_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "ROOT", tmp_path)
    fixtures.grades()
    for name in ("roster.csv", "scores.csv"):
        assert (tmp_path / "grades" / name).read_bytes() == (DATA / name).read_bytes()
