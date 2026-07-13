"""Meta 단독 수집:  python jobs/collect_meta.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import meta_collector  # noqa: E402
from jobs._runner import run_collectors  # noqa: E402

if __name__ == "__main__":
    run_collectors([("meta", meta_collector)])
