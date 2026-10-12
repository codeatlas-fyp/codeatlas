"""Record SBX issues from live Jira into sanitised fixtures (step-5 spec).

Raw responses go to the git-ignored `.recordings/raw/`; sanitised copies go to
`tests/fixtures/jira/`. The person-number mapping is kept in `.recordings/identity.json` so the
same person gets the same fake on every run. Credentials come from `.env` (never printed).

Usage: uv run python scripts/record_jira.py SBX-1 SBX-2 ... [--out tests/fixtures/jira]
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codeatlas.collect.jira.client import JiraClient  # noqa: E402
from codeatlas.collect.jira.recorder import Sanitiser, record  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keys", nargs="+")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "jira")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    base_url = env.get("CODEATLAS_JIRA_BASE_URL") or ""
    identity_file = ROOT / ".recordings" / "identity.json"
    known = json.loads(identity_file.read_text()) if identity_file.is_file() else {}
    sanitiser = Sanitiser(site_host=urlparse(base_url).netloc, known=known)

    with JiraClient(
        base_url, env.get("CODEATLAS_JIRA_EMAIL") or "", env.get("CODEATLAS_JIRA_TOKEN") or ""
    ) as client:
        record(client, args.keys, ROOT / ".recordings" / "raw")
        written = record(client, args.keys, args.out, sanitiser)

    identity_file.write_text(json.dumps(sanitiser.mapping(), indent=2))
    for path in written:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
