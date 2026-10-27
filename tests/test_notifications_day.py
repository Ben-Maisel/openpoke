"""The day-in-the-life measurement (P7) stays exact: interruptions before and after notification lanes."""

from datetime import timedelta

from evals.notifications.day import DIGEST_HEADER, run_day, scripted_day

RESULT = run_day()


def test_the_scripted_day():
    kinds = [e.kind for e in scripted_day()]
    assert (kinds.count("urgent_email"), kinds.count("important_email"), kinds.count("unimportant_email")) == (3, 7, 5)
    assert (kinds.count("reminder"), kinds.count("check_news"), kinds.count("check_nothing")) == (2, 1, 15)


def test_messages_drop_from_28_to_11():
    assert RESULT.before == 28  # 10 important emails + 2 reminders + 16 check runs, each its own message
    assert (RESULT.after, RESULT.digests) == (11, 6)


def test_urgent_emails_and_reminders_still_arrive_the_moment_they_happen():
    immediate = {text: when.strftime("%H:%M") for when, text in RESULT.deliveries if not text.startswith(DIGEST_HEADER)}
    assert immediate == {
        "Reminder: call the dentist": "09:00", "Interview moved to 3pm today": "09:10",
        "Chase verification code": "13:40", "Wire approval needed before 5pm ET": "17:05",
        "Reminder: take medication": "18:00",
    }


def test_nothing_unimportant_or_unchanged_reaches_the_user():
    everything = "\n".join(text for _, text in RESULT.deliveries)
    assert "NO_UPDATE" not in everything and "Amazon" not in everything and "J.Crew" not in everything
    assert "Alice replied: Friday works" in everything  # the one check that found news does


def test_digest_items_wait_at_most_an_hour_except_overnight():
    # 8 digest items: 7 important emails and the one check that found news
    daytime = {text: wait for text, wait in RESULT.waits.items() if "Benefits" not in text}
    assert len(RESULT.waits) == 8 and max(daytime.values()) <= timedelta(hours=1)
    assert RESULT.waits["Benefits enrollment closes Nov 15"] == timedelta(hours=8, minutes=30)


def test_no_digest_during_quiet_hours():
    for when, text in RESULT.deliveries:
        if text.startswith(DIGEST_HEADER):
            assert 7 <= when.hour < 22, when
