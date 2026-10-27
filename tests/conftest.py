"""Shared pytest setup.

This module runs before any test imports `server`, which matters because the
server's stores pick their file paths at import time. Everything below must
happen before that first import.
"""

import atexit
import os
import shutil
import tempfile

# Send all runtime data to a throwaway directory so tests never touch server/data.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="openpoke-test-data-")
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
os.environ["OPENPOKE_DATA_DIR"] = _TEST_DATA_DIR

# Pin credentials to inert values so a developer's .env can never cause real API calls.
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
os.environ["COMPOSIO_API_KEY"] = ""
os.environ["COMPOSIO_GMAIL_AUTH_CONFIG_ID"] = ""
