"""Write the G1 sample bundle and its verdict into tests/fixtures/bundles/<bundle_hash>/.

The bundle is built from schema objects in code (tests/samples.py, fake identities only), so the
fixture is generated, never typed by hand (step-3 spec, decision on reconciliation F12).

Rerun it whenever the default rules change: the stored verdict records the ruleset version.

Usage: uv run python scripts/make_sample_bundle.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from codeatlas.bundle.store import BundleStore  # noqa: E402
from codeatlas.pipeline import evaluate_bundle  # noqa: E402
from tests.samples import BUNDLE  # noqa: E402


def main() -> None:
    store = BundleStore(ROOT / "tests" / "fixtures" / "bundles")
    hash_ = store.save(BUNDLE)
    store.save_verdict(evaluate_bundle(BUNDLE, hash_))
    print(hash_)


if __name__ == "__main__":
    main()
