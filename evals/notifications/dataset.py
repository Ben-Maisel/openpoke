"""Labelled emails for the urgency eval (P7)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from server.services.gmail.processing import ProcessedEmail

DATA_DIR = Path(__file__).resolve().parent / "data"
LABELS = ("urgent", "important", "not_important")
EXPECTED_LANE = {"urgent": "now", "important": "digest", "not_important": None}


@dataclass(frozen=True)
class LabelledEmail:
    id: str
    label: str
    sender: str
    subject: str
    text: str
    why: str
    received: datetime

    def as_processed(self) -> ProcessedEmail:
        """The email exactly as the watcher hands it to the classifier."""
        return ProcessedEmail(
            id=self.id, thread_id=self.id, query="label:INBOX newer_than:10m", subject=self.subject,
            sender=self.sender, recipient="me@example.com", timestamp=self.received, label_ids=["INBOX"],
            clean_text=self.text, has_attachments=False, attachment_count=0, attachment_filenames=[],
        )


def load_emails() -> List[LabelledEmail]:
    data = json.loads((DATA_DIR / "emails.json").read_text(encoding="utf-8"))
    received = datetime.fromisoformat(data["received"])
    return [
        LabelledEmail(e["id"], e["label"], e["sender"], e["subject"], e["text"], e["why"], received)
        for e in data["emails"]
    ]


__all__ = ["DATA_DIR", "EXPECTED_LANE", "LABELS", "LabelledEmail", "load_emails"]
