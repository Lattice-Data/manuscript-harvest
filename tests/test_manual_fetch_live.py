"""Fetch each DOI in the spec for real and compare against the hand-fetched copies.

Unlike every other file in `tests/`, this one uses the network -- and, for a
paywalled journal, the library proxy and a live session. So it is off unless asked
for twice over: the spec and its bytes have to be present, and
`MANUSCRIPT_HARVEST_MANUAL_NETWORK=1` has to be set. A machine that happens to have
a `manual_fetch/` directory should not start making publisher requests during an ordinary
test run.

    MANUSCRIPT_HARVEST_MANUAL_DIR=~/manual-fetch-papers \
    MANUSCRIPT_HARVEST_MANUAL_NETWORK=1 \
    python -m pytest tests/test_manual_fetch_live.py -v

This is a diagnostic rather than a gate. It cannot run in CI -- no network, no
browser, no proxy credentials -- so what it finds should be turned into offline
fixtures in `fakes.py`, where it will keep paying off. See `manual_fetch.py` for why the
comparison treats the article PDF and its supplements by different rules.
"""

import os

import pytest

from manuscript_harvest.fetch import manual_fetch
from manuscript_harvest.fetch.cli import load_config
from manuscript_harvest.fetch.fetcher import fetch_publication

SPEC = manual_fetch.load_spec()
ARTICLES = SPEC.get("articles") or []
ENABLED = os.environ.get("MANUSCRIPT_HARVEST_MANUAL_NETWORK") == "1"

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not ARTICLES, reason=f"no {manual_fetch.spec_path()}"),
    pytest.mark.skipif(
        not ENABLED,
        reason="set MANUSCRIPT_HARVEST_MANUAL_NETWORK=1 to allow live publisher requests",
    ),
]


@pytest.mark.parametrize(
    "article",
    ARTICLES,
    ids=[a.get("source_dir") or a["doi"] for a in ARTICLES],
)
def test_fetch_matches_the_hand_fetched_copy(article, tmp_path):
    """One paper per test, so a Wiley failure does not hide a Nature success."""
    config = load_config("config.yaml")
    config.setdefault("fetch", {})["corpus_dir"] = str(tmp_path)

    # force, because a cached manifest would test the cache rather than the fetch.
    record = fetch_publication(article["doi"], config, force=True)
    directory = record.get("_directory") or tmp_path

    checks = manual_fetch.compare(article, record, directory)
    report = manual_fetch.format_checks(f"{article['doi']} ({article.get('source_dir')})", checks)
    print("\n" + report)

    failed = manual_fetch.failures(checks)
    if failed:
        pytest.fail(report + f"\n\ntiers tried: {record.get('tiers_tried')}", pytrace=False)
