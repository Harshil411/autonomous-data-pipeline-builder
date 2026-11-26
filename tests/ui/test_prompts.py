"""Ready-made requests are shown as a goal line and bullets, but sent unchanged.

The text of a request is the instruction the agents receive, and every benchmark result
was measured on it. So the bullets are a way of showing the same words, never a rewrite:
these tests hold that line for every request file in the repository.
"""

import re
from pathlib import Path

import pytest

from adpb.requests_io import load_request_file
from app.prompts import as_bullets, text_to_send

REQUESTS = sorted((Path(__file__).resolve().parents[2] / "benchmarks" / "requests").glob("*.yaml"))
SHOP = next(p for p in REQUESTS if p.name == "shop_join.yaml")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%_.,\"'()/-]*[a-z0-9]", text.lower())


def test_a_request_becomes_a_goal_line_and_one_bullet_per_rule():
    original = load_request_file(SHOP)[0].request
    lines = as_bullets(original).splitlines()
    assert lines[0] == ("Load the customers and orders CSV exports, clean them up, and join "
                        "each order to its customer.")
    assert lines[1] == ""
    bullets = lines[2:]
    assert len(bullets) == 6 and all(b.startswith("• ") for b in bullets)
    assert bullets[1] == "• Order dates come in several formats and must become real dates"
    assert bullets[-1] == "• Write the result to the database"
    assert not any(b.endswith((";", ".")) for b in bullets)


@pytest.mark.parametrize("path", REQUESTS, ids=lambda p: p.stem)
def test_bullets_keep_every_word_in_order(path):
    original = load_request_file(path)[0].request
    assert _words(as_bullets(original)) == _words(original)


@pytest.mark.parametrize("path", REQUESTS, ids=lambda p: p.stem)
def test_an_unedited_example_is_sent_exactly_as_written(path):
    original = load_request_file(path)[0].request
    assert text_to_send(as_bullets(original), original) == original
    # stray spaces or a trailing newline from the text box do not count as an edit
    assert text_to_send("  " + as_bullets(original) + "\n", original) == original


def test_an_edited_example_is_sent_as_the_visitor_typed_it():
    original = load_request_file(SHOP)[0].request
    edited = as_bullets(original) + "\n• Also drop orders under 5 dollars"
    assert text_to_send(edited, original) == edited
    assert text_to_send("My own request", None) == "My own request"


def test_short_text_is_left_alone():
    assert as_bullets("Load my files and clean them.") == "Load my files and clean them."
    assert as_bullets("") == ""


@pytest.mark.parametrize("path", REQUESTS, ids=lambda p: p.stem)
def test_a_bullet_never_breaks_inside_brackets_or_quotes(path):
    # "(tier lower-cased; missing tier stays empty)" is one remark, not two rules
    for line in as_bullets(load_request_file(path)[0].request).splitlines():
        assert line.count("(") == line.count(")"), line
        assert line.count('"') % 2 == 0, line
