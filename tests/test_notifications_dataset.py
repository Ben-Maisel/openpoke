"""Guards on the urgency eval's labelled emails (P7)."""

from collections import Counter

import pytest

from evals.notifications.dataset import EXPECTED_LANE, LABELS, load_emails

EMAILS = load_emails()


def test_balanced_classes_and_unique_ids():
    assert Counter(e.label for e in EMAILS) == {"urgent": 8, "important": 8, "not_important": 8}
    assert len({e.id for e in EMAILS}) == len(EMAILS)


@pytest.mark.parametrize("email", EMAILS, ids=lambda e: e.id)
def test_every_email_is_complete_and_explained(email):
    assert email.label in LABELS
    assert all(text.strip() for text in (email.sender, email.subject, email.text, email.why))
    assert email.id[0] == email.label[0]  # u*, i*, n*: the id says the label at a glance


def test_every_label_maps_to_a_lane():
    assert set(EXPECTED_LANE) == set(LABELS)
    assert EXPECTED_LANE["urgent"] == "now" and EXPECTED_LANE["important"] == "digest"


def test_emails_arrive_as_the_watcher_would_pass_them():
    processed = EMAILS[0].as_processed()
    assert (processed.id, processed.subject, processed.clean_text) == (EMAILS[0].id, EMAILS[0].subject, EMAILS[0].text)
    assert processed.timestamp.isoformat() == "2026-10-27T09:00:00-07:00"
