"""`python -m evals.memory`: run the long-lived worker memory eval (see run.py for options)."""

import os
import tempfile

# The eval builds workers in the app's real stores; point them at a throwaway folder
# so an eval run leaves server/data untouched.
os.environ.setdefault("OPENPOKE_DATA_DIR", tempfile.mkdtemp(prefix="openpoke-eval-data-"))

from .run import main  # noqa: E402

main()
