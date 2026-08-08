"""The selection CLI: exit codes, and the warnings that stop a bad answer being kept.

The exit codes are the contract a script reads, and they follow the two earlier
stages: 0 when the thing asked for succeeded, 1 when it completed with something a
human needs to see, 2 for "nothing to do" or a bad argument.

The warnings matter as much. `pack` on an article whose text cannot be believed still
writes a pack -- refusing would be unhelpful, since a caller may want to look -- so it
has to *say* that a "not found" over that pack means nothing. A silent pack is how a
landing page becomes a curated fact.
"""

import json

import pytest

from manuscript_harvest.extract.extractor import extract_article
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store
from manuscript_harvest.select import cli
from tests.fakes import DOI, jats_article, make_article

L = Limits()

BODY = (
    '<sec sec-type="methods"><title>Methods</title><p>'
    + "Nuclei were isolated and loaded on a 10x Chromium controller with the "
      "Single Cell 3' v3 kit at eight weeks of age. " * 60
    + "Reference data came from GSE131907.</p></sec>"
    '<sec sec-type="data-availability"><title>Data availability</title><p>Data are '
    'deposited in the Gene Expression Omnibus under accession GSE208532.</p></sec>'
)


#: The DOI of a real corpus article whose main text extracts from a saved publisher
#: landing page, so `readiness` calls it `text_unavailable`. Named rather than
#: invented so a reader can go and look at why.
LANDING_DOI = "10.1126/science.aay3224"


@pytest.fixture
def corpus(tmp_path):
    """A one-article corpus, extracted, with a config file that points at it.

    `--config` resolves against the current directory, so a test that did not pass one
    would pick up the repository's own `config.yaml` and its real corpus -- which is
    the failure `config.warn_if_config_missing` exists to warn humans about.
    """
    root = tmp_path / "corpus"
    directory = make_article(root / store.doi_slug(DOI), xml=jats_article(BODY))
    extract_article(directory, limits=L)
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {root}\n")
    return tmp_path, config, directory


def _landing_corpus(tmp_path, page=b"<html><body><p>Purchase access.</p></body></html>"):
    """A corpus holding one article that extracted from a landing page, and nothing else."""
    root = tmp_path / "corpus"
    directory = make_article(root / store.doi_slug(LANDING_DOI), doi=LANDING_DOI,
                             landing=page)
    extract_article(directory, limits=L)
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {root}\n")
    return config


def _run(config, *argv):
    return cli.main(["--config", str(config), *argv])


# -- readiness ---------------------------------------------------------------

def test_readiness_exits_zero_when_every_article_can_carry_a_negative(corpus, capsys):
    _, config, _ = corpus
    assert _run(config, "readiness") == 0
    assert "1/1 article(s) can carry a negative answer" in capsys.readouterr().err


def test_readiness_exits_one_when_an_article_cannot(tmp_path, capsys):
    """Exit 1 rather than 0: a corpus holding an article nothing can be concluded from
    is a state a batch script should be able to notice."""
    assert _run(_landing_corpus(tmp_path), "readiness") == 1
    assert "text_unavailable" in capsys.readouterr().err


def test_readiness_on_an_empty_corpus_is_nothing_to_do(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {tmp_path / 'nope'}\n")
    assert _run(config, "readiness") == 2


def test_readiness_writes_its_verdicts_as_json(corpus, tmp_path):
    _, config, _ = corpus
    out = tmp_path / "r.json"
    _run(config, "readiness", "--json", str(out))
    assert json.loads(out.read_text())[store.doi_slug(DOI)]["state"] == "ready"


# -- candidates --------------------------------------------------------------

def test_candidates_finds_both_and_claims_neither(corpus, capsys, tmp_path):
    _, config, _ = corpus
    out = tmp_path / "c.json"
    assert _run(config, "candidates", "--json", str(out)) == 0
    record = json.loads(out.read_text())[store.doi_slug(DOI)]
    assert sorted(c["accession"] for c in record["study"]) == ["GSE131907", "GSE208532"]
    assert all(c["role"] is None for c in record["study"])
    assert "own` vs `reused` is a judgement" in capsys.readouterr().err


def test_candidates_skips_an_unreadable_article_by_default(tmp_path, capsys):
    """Running the finder over a landing page harvests accessions out of an abstract
    and a reference list and presents them as the paper's, which is worse than
    producing nothing. `--include-unreadable` is there for looking, not for answering."""
    config = _landing_corpus(tmp_path, b"<html><body><p>See GSE208532.</p></body></html>")
    assert _run(config, "candidates") == 2
    assert "skipped: text_unavailable" in capsys.readouterr().err
    assert _run(config, "candidates", "--include-unreadable") == 0


def test_candidates_accepts_a_doi_a_slug_or_a_directory(corpus):
    _, config, directory = corpus
    for named in (DOI, store.doi_slug(DOI), str(directory)):
        assert _run(config, "candidates", named) == 0


def test_an_unknown_article_is_an_argument_error(corpus, capsys):
    _, config, _ = corpus
    assert _run(config, "candidates", "10.9999/nope") == 2
    assert "no article directory" in capsys.readouterr().err


# -- pack --------------------------------------------------------------------

def test_pack_writes_the_blocks_with_their_readiness(corpus, tmp_path):
    _, config, _ = corpus
    out = tmp_path / "p.json"
    assert _run(config, "pack", DOI, "--json", str(out)) == 0
    record = json.loads(out.read_text())
    assert record["readiness"] == "ready"
    assert record["blocks"] and not record["truncated"]


def test_pack_says_when_a_negative_from_it_would_mean_nothing(tmp_path, capsys):
    """The pack is still written -- a caller may want to look -- but an answer drawn
    from a landing page must not be recorded as a finding, so the warning is the whole
    point of the command's stderr."""
    page = b"<html><body><h1>T</h1><p>" + b"Abstract text. " * 200 + b"</p></body></html>"
    assert _run(_landing_corpus(tmp_path, page), "pack", LANDING_DOI) == 0
    assert "means nothing" in capsys.readouterr().err


def test_pack_on_an_unextracted_article_is_nothing_to_do(tmp_path, capsys):
    root = tmp_path / "corpus"
    make_article(root / store.doi_slug(DOI), xml=jats_article(BODY))
    config = tmp_path / "config.yaml"
    config.write_text(f"extract:\n  corpus_dir: {root}\n")
    assert _run(config, "pack", DOI) == 2
    assert "not extracted" in capsys.readouterr().err


def test_pack_reports_the_budget_it_could_not_meet(corpus, capsys):
    _, config, _ = corpus
    assert _run(config, "pack", DOI, "--budget", "200") == 0
    assert "dropped" in capsys.readouterr().err


def test_pack_filters_and_ranks_from_the_command_line(corpus, tmp_path):
    _, config, _ = corpus
    out = tmp_path / "p.json"
    _run(config, "pack", DOI, "--kinds", "paragraph", "--roles", "main_text",
         "--sections", "data_availability", "--json", str(out))
    blocks = json.loads(out.read_text())["blocks"]
    assert all(b["kind"] == "paragraph" for b in blocks)
    assert blocks[0]["section"] == "data_availability"      # ranked first, not filtered


# -- sheet and label ---------------------------------------------------------

def test_sheet_writes_a_page_covering_the_believable_articles(corpus, tmp_path, capsys):
    _, config, _ = corpus
    out = tmp_path / "labels.html"
    assert _run(config, "sheet", "--out", str(out)) == 0
    html = out.read_text()
    assert html.count('class="cand"') == 2
    assert 'class="complete"' in html
    assert "1 article(s)" in capsys.readouterr().err


def test_sheet_refuses_a_corpus_with_nothing_worth_labelling(tmp_path, capsys):
    """A `complete: true` on a landing-page article would assert something the labeller
    has no way to know, and would then count toward recall as though they did."""
    assert _run(_landing_corpus(tmp_path), "sheet", "--out", str(tmp_path / "x.html")) == 2
    assert "no article has believable text" in capsys.readouterr().err


def test_label_applies_a_downloaded_sheet(tmp_path, capsys):
    source = tmp_path / "labels.json"
    source.write_text(json.dumps({"aspect": "accessions", "labeled_by": "me",
                                  "articles": [{"slug": "s", "doi": "10.1/x",
                                                "complete": True, "missing": [],
                                                "accessions": [
        {"accession": "GSE208532", "repository": "GEO", "level": "study",
         "role": "own", "note": ""}]}]}))
    out = tmp_path / "truth"
    assert cli.main(["label", "--apply", str(source), "--truth", str(out)]) == 0
    assert json.loads((out / "s.json").read_text())["complete"] is True
    assert "1 marked complete" in capsys.readouterr().err


def test_label_with_nothing_writable_is_nothing_to_do(tmp_path):
    source = tmp_path / "labels.json"
    source.write_text(json.dumps({"articles": []}))
    assert cli.main(["label", "--apply", str(source),
                     "--truth", str(tmp_path / "t")]) == 2


# -- verify ------------------------------------------------------------------

def _answer(tmp_path, quote, block_id):
    path = tmp_path / "answer.json"
    path.write_text(json.dumps({"slug": store.doi_slug(DOI), "accessions": [
        {"accession": "GSE208532", "role": "own",
         "evidence": [{"block_id": block_id, "quote": quote}]}]}))
    return path


def _data_availability_block(directory):
    """The data-availability *paragraph*.

    Not merely the first block in that section: JATS turns the `<title>` into its own
    heading block, and pointing a quote at the heading is a real misattribution --
    which `verify` duly reported as `wrong_block` when this helper got it wrong.
    """
    from manuscript_harvest.select import query
    return next(b for b in query.load(directory)
                if b.get("section") == "data_availability"
                and b.get("kind") == "paragraph")


def test_verify_passes_a_real_quote(corpus, tmp_path, capsys):
    _, config, directory = corpus
    block = _data_availability_block(directory)
    answers = _answer(tmp_path, "under accession GSE208532", block["block_id"])
    assert _run(config, "verify", str(answers)) == 0
    assert "1/1 claim(s) verified" in capsys.readouterr().err


def test_verify_exits_one_on_an_invented_quote(corpus, tmp_path, capsys):
    """Exit 1 rather than 2: the command worked, and what it found needs a human."""
    _, config, directory = corpus
    block = _data_availability_block(directory)
    answers = _answer(tmp_path, "donors were aged 20 to 30 years at collection",
                      block["block_id"])
    assert _run(config, "verify", str(answers)) == 1
    assert "quote_not_found" in capsys.readouterr().err


def test_verify_names_the_block_a_misattributed_quote_really_came_from(corpus, tmp_path,
                                                                      capsys):
    _, config, directory = corpus
    from manuscript_harvest.select import query
    blocks = query.load(directory)
    methods = next(b for b in blocks if b.get("section") == "methods")
    availability = _data_availability_block(directory)
    answers = _answer(tmp_path, methods["text"][:80], availability["block_id"])
    assert _run(config, "verify", str(answers)) == 1
    assert "wrong_block" in capsys.readouterr().err


def test_verify_needs_to_know_which_article(corpus, tmp_path, capsys):
    _, config, _ = corpus
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"accessions": []}))
    assert _run(config, "verify", str(path)) == 2
    assert "no article" in capsys.readouterr().err


def test_verify_writes_its_result_as_json(corpus, tmp_path):
    _, config, directory = corpus
    block = _data_availability_block(directory)
    answers = _answer(tmp_path, "accession GSE208532", block["block_id"])
    out = tmp_path / "v.json"
    _run(config, "verify", str(answers), "--json", str(out))
    assert json.loads(out.read_text())["verified"] == 1


# -- eval --------------------------------------------------------------------

def _truth(tmp_path, **overrides):
    directory = tmp_path / "truth"
    directory.mkdir(exist_ok=True)
    record = {"slug": store.doi_slug(DOI), "doi": DOI, "aspect": "accessions",
              "complete": True, "accessions": [
                  {"accession": "GSE208532", "role": "own"},
                  {"accession": "GSE131907", "role": "reused"}], **overrides}
    (directory / f"{record['slug']}.json").write_text(json.dumps(record))
    return directory


def test_eval_scores_the_baseline_against_the_labels(corpus, tmp_path, capsys):
    """The naive finder calls both accessions deposits, so precision is 0.5 on this
    article and the confusion names why."""
    _, config, _ = corpus
    assert _run(config, "eval", "--truth", str(_truth(tmp_path)), "--baseline") == 0
    err = capsys.readouterr().err
    assert "precision 0.500" in err
    assert "called_own_but_reused=1" in err


def test_eval_scores_an_answer_directory(corpus, tmp_path, capsys):
    _, config, _ = corpus
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "a.json").write_text(json.dumps({"slug": store.doi_slug(DOI),
                                                "accessions": [
        {"accession": "GSE208532", "role": "own"},
        {"accession": "GSE131907", "role": "reused"}]}))
    assert _run(config, "eval", str(answers), "--truth", str(_truth(tmp_path))) == 0
    assert "precision 1.000  recall 1.000" in capsys.readouterr().err


def test_eval_needs_either_answers_or_a_baseline(corpus, tmp_path, capsys):
    _, config, _ = corpus
    assert _run(config, "eval", "--truth", str(_truth(tmp_path))) == 2
    assert "or --baseline" in capsys.readouterr().err


def test_eval_without_labels_is_nothing_to_do(corpus, tmp_path):
    _, config, _ = corpus
    assert _run(config, "eval", "--truth", str(tmp_path / "none"), "--baseline") == 2


def test_eval_fail_under_makes_it_a_gate(corpus, tmp_path, capsys):
    _, config, _ = corpus
    truth_dir = _truth(tmp_path)
    assert _run(config, "eval", "--truth", str(truth_dir), "--baseline",
                "--fail-under", "0.9") == 1
    assert "below --fail-under" in capsys.readouterr().err
    assert _run(config, "eval", "--truth", str(truth_dir), "--baseline",
                "--fail-under", "0.4") == 0


def test_eval_names_a_label_it_had_no_prediction_for(corpus, tmp_path, capsys):
    """A label with nothing scored against it is not a pass; silently averaging over
    the articles that happened to be answered is how a shrinking denominator reads as
    an improvement."""
    _, config, _ = corpus
    truth_dir = _truth(tmp_path)
    (truth_dir / "other.json").write_text(json.dumps(
        {"slug": "10.9_other", "complete": True,
         "accessions": [{"accession": "GSE1", "role": "own"}]}))
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "a.json").write_text(json.dumps(
        {"slug": store.doi_slug(DOI), "accessions": []}))
    _run(config, "eval", str(answers), "--truth", str(truth_dir))
    assert "no prediction for 10.9_other" in capsys.readouterr().err


def test_eval_writes_the_per_article_rows_as_json(corpus, tmp_path):
    _, config, _ = corpus
    out = tmp_path / "e.json"
    _run(config, "eval", "--truth", str(_truth(tmp_path)), "--baseline",
         "--json", str(out))
    result = json.loads(out.read_text())
    assert len(result["per_article"]) == 1
    assert result["per_article"][0]["called_own_but_reused"] == ["GSE131907"]
