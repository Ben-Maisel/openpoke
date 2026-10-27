"""Tests for the bounded store of Gmail message IDs the email watcher has processed."""

import json

import pytest

from server.services.gmail.seen_store import GmailSeenStore


@pytest.fixture
def path(tmp_path):
    return tmp_path / "gmail_seen.json"


def test_new_store_is_empty(path):
    store = GmailSeenStore(path)
    assert store.has_entries() is False
    assert store.is_seen("m-1") is False


def test_marked_ids_are_seen_and_persisted(path):
    GmailSeenStore(path).mark_seen(["m-1", "m-2"])
    reloaded = GmailSeenStore(path)
    assert reloaded.is_seen("m-1") and reloaded.is_seen("m-2")
    assert json.loads(path.read_text()) == ["m-1", "m-2"]


def test_ids_are_trimmed_and_blanks_ignored(path):
    store = GmailSeenStore(path)
    store.mark_seen(["  m-1  ", "", None, "   "])
    assert store.snapshot() == ["m-1"]
    assert store.is_seen(" m-1 ")
    assert store.is_seen("") is False and store.is_seen(None) is False


def test_marking_only_blanks_does_not_write_a_file(path):
    GmailSeenStore(path).mark_seen(["", None])
    assert not path.exists()


def test_duplicates_are_stored_once(path):
    store = GmailSeenStore(path)
    store.mark_seen(["m-1", "m-1", "m-2", "m-1"])
    assert store.snapshot() == ["m-2", "m-1"]


def test_oldest_ids_are_evicted_past_the_cap(path):
    store = GmailSeenStore(path, max_entries=3)
    store.mark_seen(["a", "b", "c", "d"])
    assert store.snapshot() == ["b", "c", "d"]
    assert store.is_seen("a") is False


def test_re_marking_refreshes_recency_so_it_survives_eviction(path):
    store = GmailSeenStore(path, max_entries=3)
    store.mark_seen(["a", "b", "c"])
    store.mark_seen(["a"])        # a becomes newest
    store.mark_seen(["d"])        # evicts b, the oldest
    assert store.snapshot() == ["c", "a", "d"]


def test_load_keeps_only_newest_entries_up_to_cap(path):
    path.write_text(json.dumps(["a", "b", "c", "d", "e"]))
    assert GmailSeenStore(path, max_entries=2).snapshot() == ["d", "e"]


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', '"just a string"', ""])
def test_corrupt_file_starts_empty_instead_of_crashing(path, content):
    path.write_text(content)
    store = GmailSeenStore(path)
    assert store.snapshot() == []
    store.mark_seen(["m-1"])  # and it recovers on the next write
    assert json.loads(path.read_text()) == ["m-1"]


def test_clear_empties_store_and_file(path):
    store = GmailSeenStore(path)
    store.mark_seen(["m-1"])
    store.clear()
    assert store.has_entries() is False
    assert json.loads(path.read_text()) == []
