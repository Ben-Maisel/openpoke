"""`python -m evals.routing`: run the routing eval (see run.py for options)."""

import os
import tempfile

# The eval never needs the app's stores, but importing the server creates them.
# Point them at a throwaway folder so an eval run leaves server/data untouched.
os.environ.setdefault("OPENPOKE_DATA_DIR", tempfile.mkdtemp(prefix="openpoke-eval-data-"))

from .run import main  # noqa: E402

main()
