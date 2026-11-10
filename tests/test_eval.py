import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import run_eval  # noqa: E402


def test_eval_dev_set():
    assert run_eval.main(["--set", "questions"]) == 0


def test_eval_holdout_set():
    assert run_eval.main(["--set", "holdout"]) == 0
