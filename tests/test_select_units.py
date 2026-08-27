"""The selection stage: readiness, the section rule, the finder, and the verifier.

These pin four claims the stage rests on, each of which was measured on the
development corpus before it was written down.

1. An article with no believable body cannot be said to have deposited nothing.
   Nine of the 37 corpus directories are in that state.
2. A section filter must never be a filter. 599 of 4,009 main-text paragraphs carry
   no section, including 39 of the 86 in 10.1126/science.abo0510, which is otherwise
   `ready`.
3. The accession finder is near-exhaustive and says nothing about role. On
   10.1002/ctm2.1356 it finds five where one is the paper's own deposit.
4. A quote is verified against the block it cites, not against the article, and how
   tolerantly it matched is part of the answer.
"""

import json

import pytest

from manuscript_harvest.extract.extractor import extract_article
from manuscript_harvest.extract.limits import Limits
from manuscript_harvest.fetch import store
from manuscript_harvest.select import (candidates, cli, query, readiness, sheet,
                                       truth, verify)
from tests.fakes import DOI, jats_article, make_article, make_pdf_pages

L = Limits()

#: A believable article: a real Methods section long enough to clear
#: `min_main_text_chars`, with a data-availability statement naming one deposit and
#: one reused dataset. The shape of 10.1002/ctm2.1356, which is the case the whole
#: aspect is calibrated on.
DEPOSIT_BODY = (
    '<sec sec-type="methods"><title>Methods</title><p>'
    + "Islets from eight-week-old male C57BL/6 mice were dissociated and loaded on "
      "a 10x Chromium controller with the Single Cell 3' v3 kit. " * 60
    + "Reference data were downloaded from the Gene Expression Omnibus (GSE131907) "
      "for cell-type annotation.</p></sec>"
    '<sec sec-type="data-availability"><title>Data availability</title>'
    '<p>The dataset supporting the conclusions of this article is available in the '
    'Gene Expression Omnibus under the accession GSE208532.</p></sec>'
)


def _extracted(tmp_path, **kwargs):
    directory = make_article(tmp_path / store.doi_slug(DOI), **kwargs)
    return directory, extract_article(directory, limits=L)


def _blocks(directory):
    return query.load(directory)


# -- readiness: what a "not found" is allowed to mean ------------------------

def test_a_real_article_is_ready(tmp_path):
    directory, _ = _extracted(tmp_path, xml=jats_article(DEPOSIT_BODY))
    verdict = readiness.assess(directory)
    assert verdict["state"] == readiness.READY
    assert verdict["gaps"] == []
    assert readiness.trustworthy(verdict)


def test_a_landing_page_is_not_a_body_to_have_found_nothing_in(tmp_path):
    """10.1126/science.aay3224 and 10.1016/j.cell.2019.08.008 both extract from a
    saved publisher landing page. Reporting "no accessions deposited" for either is
    a statement about a page of abstract and references, and it used to be
    indistinguishable from the same answer about a fully extracted paper."""
    directory, record = _extracted(tmp_path, landing=b"<html><body><h1>Article</h1>"
                                                     b"<p>Purchase access.</p></body></html>")
    verdict = readiness.assess(directory)
    assert verdict["state"] == readiness.TEXT_UNAVAILABLE
    assert not readiness.trustworthy(verdict)


def test_an_unextracted_article_is_not_an_empty_one(tmp_path):
    directory = make_article(tmp_path / store.doi_slug(DOI),
                             xml=jats_article(DEPOSIT_BODY))
    assert readiness.assess(directory)["state"] == readiness.NOT_EXTRACTED


def test_an_unfetched_directory_says_so(tmp_path):
    (tmp_path / "empty").mkdir()
    assert readiness.assess(tmp_path / "empty")["state"] == readiness.NOT_FETCHED


@pytest.mark.parametrize("caveat,gap", [
    ("supplements_expected_but_missing", "supplements_expected_but_missing"),
    ("supplement_set_unverified", "supplement_set_unverified"),
    ("manifest_entry_without_a_path", "manifest_entry_without_a_path"),
])
def test_each_fetch_caveat_becomes_a_gap_that_bounds_the_answer(caveat, gap):
    """The gap vocabulary is what a negative answer carries so it states its own
    bound. `supplement_set_unverified` deliberately does not disqualify: it is the
    ordinary outcome for any page-scraping fetch tier, and holding out for a clean
    `ready` would refuse to answer anything about most of the corpus."""
    verdict = readiness.assess(
        "irrelevant",
        extraction={"status": "complete", "caveats": [caveat],
                    "main_text": {"status": "ok", "blocks": 40}},
        manifest={"slug": "s", "doi": "10.1038/x"})
    assert verdict["state"] == readiness.READY_WITH_CAVEATS
    assert gap in verdict["gaps"]
    assert verdict["why"]
    assert readiness.trustworthy(verdict)


@pytest.mark.parametrize("confidence", ["low", "none"])
def test_weak_section_labelling_is_reported_as_a_gap(confidence):
    """A section filter cannot be relied on for such an article, which is why
    `query.prefer` ranks instead of filtering. The gap is what tells a consumer that
    the ranking, not the filter, is what found the answer."""
    verdict = readiness.assess(
        "irrelevant",
        extraction={"status": "complete", "caveats": [],
                    "main_text": {"status": "ok", "blocks": 40,
                                  "section_labelling": {"confidence": confidence,
                                                        "why": "no headings matched"}}},
        manifest={"slug": "s", "doi": "10.1038/x"})
    assert verdict["gaps"] == [f"section_labelling_{confidence}"]
    assert readiness.trustworthy(verdict)


def test_a_passed_in_extraction_is_used_rather_than_re_read(tmp_path):
    """`eval` over a corpus reads each record once and passes it down. A signature that
    silently re-read from disk would make that a lie."""
    verdict = readiness.assess(tmp_path,
                               extraction={"status": "failed", "caveats": [],
                                           "main_text": {}},
                               manifest={"slug": "s", "doi": "10.1038/x"})
    assert verdict["state"] == readiness.TEXT_UNAVAILABLE
    assert verdict["doi"] == "10.1038/x"


def test_lost_supplement_text_is_a_gap_not_a_disqualification(tmp_path):
    """An unparseable supplement bounds an answer without invalidating it: the body
    was read, and the gap says which file was not. Blocking on it would refuse to
    answer anything about the many corpus articles carrying one `.rtf`."""
    directory, _ = _extracted(tmp_path, xml=jats_article(DEPOSIT_BODY),
                              supplements=[("notes.rtf", b"{\\rtf1 donor ages}")])
    verdict = readiness.assess(directory)
    assert verdict["state"] == readiness.READY_WITH_CAVEATS
    assert "supplement_text_unread" in verdict["gaps"]
    assert readiness.trustworthy(verdict)


# -- the section rule --------------------------------------------------------

def test_prefer_ranks_and_never_drops():
    blocks = [{"section": "results", "text": "r"}, {"section": None, "text": "n"},
              {"section": "methods", "text": "m"}]
    ranked = query.prefer(blocks, ["methods"])
    assert [b["text"] for b in ranked] == ["m", "n", "r"]
    assert len(ranked) == len(blocks)


def test_an_unlabelled_block_outranks_a_known_other_section():
    """The load-bearing half of the rule. A null section is missing information about
    a block, not information that the block is elsewhere -- so it must be read before
    a paragraph known to be Introduction. 39 of the 86 main-text paragraphs of
    10.1126/science.abo0510 are null and that article is `ready`."""
    blocks = [{"section": "introduction", "text": "cited work"},
              {"section": None, "text": "deposited under GSE208532"}]
    assert query.prefer(blocks, ["data_availability"])[0]["text"].startswith("deposited")


def test_a_section_preference_finds_a_statement_a_filter_would_lose(tmp_path):
    """The end-to-end version, over a PDF whose deposit sentence sits under no heading
    the labeller recognises -- which is the state of 39 of the 86 main-text paragraphs
    of 10.1126/science.abo0510. Give the extractor a real "Data availability."
    heading and it labels the section correctly; the point here is what happens when
    there isn't one."""
    page = ("The sequencing data reported in this paper have been deposited in the "
            "Gene Expression Omnibus under accession GSE208532. ") * 12
    directory, _ = _extracted(tmp_path, fulltext=make_pdf_pages([[page]]))
    blocks = _blocks(directory)
    assert all(b["section"] is None for b in blocks if b["role"] == "main_text")

    filtered = query.select(blocks, sections=["data_availability"])
    assert filtered == []                                   # what a hard filter gives
    ranked = query.prefer(blocks, ["data_availability"])
    found = candidates.find(ranked)
    assert [c.accession for c in found] == ["GSE208532"]     # what ranking gives


def test_exclude_sections_drops_outright():
    blocks = [{"section": "references", "text": "GSE111111"},
              {"section": "methods", "text": "GSE222222"}]
    kept = query.select(blocks, exclude_sections=["references"])
    assert [b["text"] for b in kept] == ["GSE222222"]


def test_select_filters_on_role_file_and_content():
    blocks = [
        {"role": "main_text", "source_file": "fulltext.nxml", "text": "donor age 62"},
        {"role": "supplement", "source_file": "supplementary/01_s1.xlsx",
         "text": "donor age 41"},
        {"role": "non_evidence", "source_file": "supplementary/02_peer.pdf",
         "text": "reviewer comments"},
    ]
    assert len(query.select(blocks, roles=["main_text", "supplement"])) == 2
    assert len(query.select(blocks, files=["supplementary/"])) == 2
    assert len(query.select(blocks, contains="DONOR AGE")) == 2   # case-insensitive


def test_provenance_carries_the_handle_and_not_the_text():
    """A record that repeats the paragraph it cites is most of a second copy of the
    article; `block_id` is the handle and `by_id` turns it back into text."""
    block = {"block_id": "abc", "kind": "paragraph", "role": "main_text",
             "section": "methods", "source_file": "fulltext.nxml",
             "locator": "body/sec[2]/p[1]", "label": None, "text": "a long paragraph"}
    assert "text" not in query.provenance(block)
    assert query.provenance(block)["locator"] == "body/sec[2]/p[1]"


def test_by_id_resolves_a_citation_back_to_its_block():
    blocks = [{"block_id": "a", "text": "one"}, {"block_id": "b", "text": "two"},
              {"text": "no id at all"}]
    assert query.by_id(blocks) == {"a": blocks[0], "b": blocks[1]}


# -- packing -----------------------------------------------------------------

def test_pack_records_what_the_budget_dropped():
    blocks = [{"kind": "paragraph", "text": "a" * 40} for _ in range(5)]
    packed = query.pack(blocks, budget=100)
    assert len(packed.blocks) == 2
    assert (packed.dropped, packed.dropped_chars, packed.truncated) == (3, 120, True)
    assert packed.considered == 5


def test_an_oversized_block_does_not_starve_the_rest_of_the_pack():
    """Stopping at the first block too big to fit is the more order-faithful rule and
    is the wrong one: a card ranked first that overruns the budget would return an
    empty pack for an article with the answer in its second block."""
    blocks = [{"kind": "paragraph", "text": "x" * 90, "block_id": "x"},
              {"kind": "paragraph", "text": "y" * 90, "block_id": "y"},
              {"kind": "paragraph", "text": "z" * 5, "block_id": "z"}]
    packed = query.pack(blocks, budget=100)
    assert [b["block_id"] for b in packed.blocks] == ["x", "z"]
    assert packed.dropped_ids == ["y"]


def test_the_reordering_a_skip_causes_is_recorded_not_just_counted():
    """A pack holding a lower-ranked block while a higher-ranked one was dropped is
    the silent reordering that makes a wrong negative look considered. A consumer that
    got "no accessions found" can check whether the data-availability block was one of
    the casualties."""
    blocks = [{"kind": "paragraph", "text": "a" * 80, "block_id": "wanted"},
              {"kind": "paragraph", "text": "b" * 5, "block_id": "spare"}]
    packed = query.pack(blocks, budget=50)
    assert packed.dropped_ids == ["wanted"]
    assert packed.to_dict()["dropped_ids"] == ["wanted"]


def test_a_table_card_is_dropped_whole_rather_than_part_filled():
    """Half a column list reads exactly like a complete one, so a card that does not
    clear `MIN_CARD_HEADROOM` is refused rather than squeezed in."""
    blocks = [{"kind": "paragraph", "text": "p" * 9_800},
              {"kind": "table", "text": "TABLE: S1\nColumns (35): ..."}]
    packed = query.pack(blocks, budget=10_000)
    assert [b["kind"] for b in packed.blocks] == ["paragraph"]
    assert packed.dropped == 1


def test_a_pack_is_byte_stable():
    blocks = [{"kind": "paragraph", "text": "a", "block_id": "1"}]
    assert query.dump(query.pack(blocks)) == query.dump(query.pack(blocks))


# -- the finder --------------------------------------------------------------

def test_the_finder_gets_the_accessions_and_assigns_no_role(tmp_path):
    """The measurement the stage is arranged around. Both accessions are found; the
    finder says nothing about which is the paper's own, because on
    10.1002/ctm2.1356 that distinction is four false positives out of five."""
    directory, _ = _extracted(tmp_path, xml=jats_article(DEPOSIT_BODY))
    found = candidates.find(_blocks(directory))
    assert sorted(c.accession for c in found) == ["GSE131907", "GSE208532"]
    assert all(c.role is None for c in found)


def test_a_mention_carries_the_sentence_that_decides_the_role(tmp_path):
    directory, _ = _extracted(tmp_path, xml=jats_article(DEPOSIT_BODY))
    by_accession = {c.accession: c for c in candidates.find(_blocks(directory))}
    assert "supporting the conclusions" in by_accession["GSE208532"].mentions[0]["sentence"]
    assert "downloaded from" in by_accession["GSE131907"].mentions[0]["sentence"]


def test_mentions_are_pooled_per_accession_not_per_occurrence():
    blocks = [{"block_id": "a", "text": "deposited under GSE208532.", "kind": "paragraph"},
              {"block_id": "b", "text": "see GSE208532 for the data.", "kind": "paragraph"}]
    found = candidates.find(blocks)
    assert len(found) == 1
    assert [m["block_id"] for m in found[0].mentions] == ["a", "b"]


@pytest.mark.parametrize("accession,repository,level", [
    ("GSE208532", "GEO", candidates.STUDY),
    ("GSM1839192", "GEO", candidates.SAMPLE),
    ("PRJNA123456", "BioProject", candidates.STUDY),
    ("E-MTAB-8145", "ArrayExpress", candidates.STUDY),
    ("phs001783.v8.p1", "dbGaP", candidates.STUDY),
    ("EGAS00001002325", "EGA", candidates.STUDY),
    ("SAMEA5689352", "BioSample", candidates.SAMPLE),
    ("ERS3493332", "ENA", candidates.SAMPLE),
    ("HRA001149", "GSA", candidates.STUDY),
    ("PXD029501", "ProteomeXchange", candidates.STUDY),
    ("syn12345678", "Synapse", candidates.STUDY),
])
def test_each_repository_family_is_recognised(accession, repository, level):
    found = candidates.find([{"block_id": "x", "kind": "paragraph",
                              "text": f"available at {accession} today."}])
    assert [(c.accession, c.repository, c.level) for c in found] \
        == [(accession, repository, level)]


def test_a_dbgap_version_survives_the_sentence_walk():
    """`phs001783.v8.p1` contains two full stops, and a sentence walk that treated
    every "." as a boundary cut the accession in half. Both corpus dbGaP ids are
    versioned."""
    found = candidates.find([{"block_id": "x", "kind": "paragraph",
                              "text": "Data are in dbGaP under phs001783.v8.p1 now."}])
    assert [c.accession for c in found] == ["phs001783.v8.p1"]
    assert "phs001783.v8.p1" in found[0].mentions[0]["sentence"]


@pytest.mark.parametrize("text", [
    "the GPL570 platform was used",              # a platform is never a deposit
    "synaptic density (syn) was measured",       # too few digits for Synapse
    "GSE12 was not a real series",               # too few digits for GEO
])
def test_deliberate_non_matches(text):
    assert candidates.find([{"block_id": "x", "kind": "paragraph", "text": text}]) == []


def test_sample_level_ids_are_kept_out_of_what_gets_adjudicated():
    """On 10.1126/science.aat5031 the per-sample ENA ids sit in a table card whose
    column enumeration stops at `limits.max_unique_values`, so the finder sees five
    of a column the card itself reports as 43. Whatever it returns is a sample of a
    cap, not the paper's deposit."""
    card = ("TABLE: S1\n  1. European Nucleotide Archive code [text, 43 distinct] "
            "e.g. ERS3493332 (SAMEA5689352), ERS3493333 (SAMEA5689353)")
    found = candidates.find([
        {"block_id": "c", "kind": "table", "text": card},
        {"block_id": "p", "kind": "paragraph", "text": "Deposited under GSE208532."}])
    split = candidates.by_level(found)
    assert [c.accession for c in split[candidates.STUDY]] == ["GSE208532"]
    assert len(split[candidates.SAMPLE]) == 4
    assert candidates.naive_own(found) == ["GSE208532"]


def test_a_table_card_mention_quotes_the_line_not_the_card():
    """A card has no sentences -- it is one fact per line -- so a sentence walk over
    one returns a paragraph of unrelated columns."""
    card = "TABLE: S1\n  1. sex [text] = F | M\n  2. GEO [text] = GSE208532\n  3. age"
    found = candidates.find([{"block_id": "c", "kind": "table", "text": card}])
    assert found[0].mentions[0]["sentence"] == "2. GEO [text] = GSE208532"


def test_references_are_excluded_by_the_cli_helper(tmp_path):
    """An accession in a reference list belongs to the paper being cited."""
    body = jats_article(DEPOSIT_BODY, back='<ref-list><ref><mixed-citation>'
                                           'Data at GSE999999.</mixed-citation>'
                                           '</ref></ref-list>')
    directory, _ = _extracted(tmp_path, xml=body)
    assert "GSE999999" not in {c.accession for c in cli._find_in(directory)}


# -- the verifier ------------------------------------------------------------

def test_an_exact_quote_verifies_exactly():
    blocks = {"a": {"text": "Deposited under GSE208532 in GEO."}}
    assert verify.verify_quote("under GSE208532", "a", blocks) == {
        "block_id": "a", "verdict": verify.EXACT, "verified": True}


@pytest.mark.parametrize("quote,text,level", [
    ("aged 40-84 years", "cohort aged 40–84 years total", verify.NORMALIZED),
    ('we used "anti-CD3"', 'we used “anti-CD3” here', verify.NORMALIZED),
    ("deposited in the Gene Expression Omnibus",
     "deposited in the Gene\nExpression   Omnibus", verify.NORMALIZED),
    ("cell-type annotation", "cell­type annotation was done", verify.LOOSE),
])
def test_publisher_typography_does_not_fail_a_real_quote(quote, text, level):
    """En dashes in ranges, curly quotes, a PDF's line breaks and a soft hyphen inside
    a hyphenated word all look identical and compare unequal."""
    assert verify.find_quote(quote, text)[0] == level


def test_a_repaired_superscript_verifies_fuzzily_at_sentence_length():
    """PMC drops superscripts, so a block holds "1 x 10cells" where the paper printed
    "1 x 10^6 cells". A model that silently repairs the exponent while quoting must
    still be verifiable -- as `fuzzy`, which is a weaker claim and reads as one."""
    quote = ("cells were plated at 1 x 10^6 cells per well in medium containing "
             "10% serum overnight")
    text = ("cells were plated at 1 x 10cells per well in medium containing "
            "10% serum overnight")
    verdict, detail = verify.find_quote(quote, text)
    assert verdict == verify.FUZZY
    assert detail["coverage"] > verify.FUZZY_THRESHOLD


def test_fuzzy_matching_is_refused_on_a_short_string():
    """"abcd" against "abXcd" scores coverage 1.00 and a longest run of 0.5, clearing
    both thresholds on what is not a quotation. Below `FUZZY_MIN_CHARS` a quote must
    match at `loose` or better."""
    assert verify.find_quote("abcd", "abXcd")[0] == verify.NOT_FOUND
    assert verify.find_quote("1 x 10^6 cells", "1 x 10cells")[0] == verify.NOT_FOUND


@pytest.mark.parametrize("quote", ["", "   ", "\n"])
def test_an_empty_quote_is_its_own_verdict(quote):
    """Not `not_found`: a claim with a blank quote is a malformed claim, and reporting
    it as "the text was not in the paper" would send someone looking for a quote that
    was never made."""
    assert verify.find_quote(quote, "any text")[0] == verify.EMPTY_QUOTE


def test_normalize_keeps_case_because_identifiers_carry_it():
    """`GSE208532` and `gse208532` are the same accession, but `TP53` and `Tp53` are a
    human gene and a mouse one. Case goes at the `loose` level, where it has already
    been decided that precision is being traded for tolerance."""
    assert verify.normalize("GSE208532") == "GSE208532"
    assert verify.loosen("GSE208532") == "gse208532"


def test_an_invented_quote_is_not_found():
    blocks = {"a": {"text": "The mice were eight weeks old at sacrifice."}}
    assert verify.verify_quote("donors were aged 20 to 30 years", "a",
                               blocks)["verdict"] == verify.NOT_FOUND


def test_a_quote_real_somewhere_else_is_wrong_block_not_verified():
    """This is why verification is per block and not per article. The text is genuinely
    in the paper, so an article-level substring check passes it -- and cannot say the
    model attributed a Methods sentence to a peer-review file."""
    blocks = {"methods": {"text": "Donors were aged 40 to 84 years at collection."},
              "review": {"text": "The reviewers asked for a power calculation."}}
    result = verify.verify_quote("aged 40 to 84 years", "review", blocks)
    assert result["verdict"] == verify.WRONG_BLOCK
    assert result["found_in"] == "methods"
    assert not result["verified"]


def test_no_search_stops_at_the_cited_block():
    blocks = {"a": {"text": "nothing here"}, "b": {"text": "aged 40 to 84 years"}}
    assert verify.verify_quote("aged 40 to 84 years", "a", blocks,
                               search_all=False)["verdict"] == verify.NOT_FOUND


def test_a_citation_to_a_block_that_does_not_exist():
    assert verify.verify_quote("x", "nope", {"a": {"text": "x"}})["verdict"] \
        == verify.NO_SUCH_BLOCK


def test_a_claim_is_only_as_good_as_its_weakest_quote():
    """A claim resting partly on text that is not there is not partly true."""
    blocks = {"a": {"text": "Deposited under GSE208532."}}
    result = verify.verify_claims(
        [{"accession": "GSE208532", "evidence": [
            {"block_id": "a", "quote": "Deposited under GSE208532."},
            {"block_id": "a", "quote": "and also in dbGaP"}]}], blocks)
    assert result["claims"][0]["verdict"] == verify.NOT_FOUND
    assert result["unverified"] == 1


def test_a_claim_with_no_evidence_at_all_is_counted():
    result = verify.verify_claims([{"accession": "GSE1"}], {"a": {"text": "x"}})
    assert result["counts"]["no_evidence"] == 1
    assert not result["claims"][0]["verified"]


def test_a_single_flat_quote_is_accepted_as_well_as_an_evidence_list():
    """The flat `{block_id, quote}` shape is what a one-quote answer naturally writes,
    and refusing it would push the reshaping into every caller."""
    blocks = {"a": {"text": "Deposited under GSE208532 in GEO."}}
    result = verify.verify_claims(
        [{"accession": "GSE208532", "block_id": "a", "quote": "under GSE208532"}], blocks)
    assert result["verified"] == 1


def test_the_fuzzy_measurements_travel_with_a_fuzzy_verification():
    """A consumer choosing to accept `fuzzy` needs to see how far it stretched; a
    consumer refusing it needs only the verdict."""
    quote = ("cells were plated at 1 x 10^6 cells per well in medium containing "
             "10% serum overnight")
    blocks = {"a": {"text": "cells were plated at 1 x 10cells per well in medium "
                            "containing 10% serum overnight"}}
    record = verify.verify_quote(quote, "a", blocks)
    assert record["verdict"] == verify.FUZZY and record["verified"]
    assert "coverage" in record and "longest_run" in record


# -- truth and scoring -------------------------------------------------------

def test_the_naive_baseline_scores_exactly_what_it_costs():
    """The number a change has to beat, computed rather than remembered. On
    10.1002/ctm2.1356 the finder returns five accessions and one is the deposit."""
    label = {"slug": "10.1002_ctm2.1356", "complete": True, "accessions": [
        {"accession": "GSE208532", "role": truth.OWN},
        {"accession": "GSE131907", "role": truth.REUSED},
        {"accession": "GSE146026", "role": truth.REUSED},
        {"accession": "GSE136831", "role": truth.REUSED},
        {"accession": "GSE113197", "role": truth.REUSED}]}
    row = truth.score_one([a["accession"] for a in label["accessions"]], label)
    assert row["precision"] == pytest.approx(0.2)
    assert row["recall"] == 1.0
    assert len(row["called_own_but_reused"]) == 4


def test_recall_is_not_computed_over_an_incomplete_label():
    """Without the labeller's assertion that they looked for others, a missed
    accession and an accession nobody got to are the same file on disk. `None` rather
    than 1.0, so a half-labelled article cannot inflate an average by looking
    perfect."""
    label = {"slug": "s", "complete": False,
             "accessions": [{"accession": "GSE1", "role": truth.OWN}]}
    assert truth.score_one(["GSE1"], label)["recall"] is None
    assert truth.score_one(["GSE1"], label)["precision"] == 1.0

    result = truth.score({"s": {"GSE1"}}, {"s": label})
    assert (result["articles"], result["articles_partial"]) == (1, 1)
    assert result["recall"] is None


def test_scoring_is_micro_averaged():
    """A macro average lets a one-accession article weigh as much as a ten-accession
    one, and 10.1016/j.isci.2023.106877 alone carries ten of the corpus's
    candidates."""
    labels = {
        "small": {"slug": "small", "complete": True,
                  "accessions": [{"accession": "A", "role": truth.OWN}]},
        "big": {"slug": "big", "complete": True, "accessions":
                [{"accession": f"B{n}", "role": truth.OWN} for n in range(9)]},
    }
    result = truth.score({"small": {"A"}, "big": set()}, labels)
    assert result["recall"] == pytest.approx(0.1)          # 1 of 10, not (1.0+0.0)/2

def test_a_prediction_without_a_label_is_named_not_scored():
    labels = {"a": {"slug": "a", "complete": True, "accessions": []}}
    result = truth.score({"a": set(), "b": {"GSE1"}}, labels)
    assert result["predictions_without_label"] == ["b"]
    assert result["articles"] == 1


def test_own_set_reads_a_label_and_an_answer_identically():
    record = {"accessions": [{"accession": "A", "role": truth.OWN},
                             {"accession": "B", "role": truth.REUSED}]}
    assert truth.own_set(record) == {"A"}


# -- the labelling sheet -----------------------------------------------------

def test_the_sheet_leaves_out_articles_a_label_would_be_meaningless_for():
    entries = [
        {"slug": "good", "candidates": [], "verdict": {"state": readiness.READY}},
        {"slug": "landing", "candidates": [],
         "verdict": {"state": readiness.TEXT_UNAVAILABLE}}]
    assert [e["slug"] for e in sheet.articles_worth_labelling(entries)] == ["good"]


def test_every_article_gets_a_complete_box_including_the_empty_ones():
    """The empty articles are where `complete` does all the work: an empty result from
    a broken pattern list and an empty result from a paper that deposited nothing are
    the same file otherwise."""
    html = sheet.render([{"slug": "empty", "doi": "10.1038/x", "candidates": [],
                          "verdict": {"state": readiness.READY}}])
    assert html.count('class="complete"') == 1
    assert 'class="missing"' in html


def test_the_sheet_asks_about_study_ids_and_only_lists_sample_ids():
    found = candidates.find([
        {"block_id": "p", "kind": "paragraph",
         "text": "Deposited at GSE208532; samples GSM1839192 and GSM1839193."}])
    html = sheet.render([{"slug": "s", "doi": "10.1038/x", "candidates": found,
                          "verdict": {"state": readiness.READY}}])
    assert html.count('class="cand"') == 1              # one radio group, not three
    assert "GSM1839192" in html                         # but the sample ids are shown
    assert "2 sample-level id(s)" in html


def test_the_download_payload_is_parseable_json_not_html_escaped():
    """`html.escape` on a payload inside a `<script>` element is a real bug this
    package has already shipped once: HTML5 script data does not expand character
    references, so `&quot;` reaches `JSON.parse` literally and Download throws."""
    html = sheet.render([{"slug": "s", "doi": '10.1038/a"b<c', "candidates": [],
                          "verdict": {"state": readiness.READY}}])
    body = html.split('<script type="application/json" id="meta">')[1].split("</script>")[0]
    assert json.loads(body) == {"articles": 1, "candidates": 0}
    assert "&quot;" not in body


def test_the_sheet_escapes_a_doi_in_the_visible_body():
    html = sheet.render([{"slug": "s", "doi": "10.1038/<script>x", "candidates": [],
                          "verdict": {"state": readiness.READY}}])
    assert "<script>x" not in html.split("</style>")[1].split("<footer")[0]


def test_every_hook_the_download_script_reads_is_in_the_markup():
    """The sheet is two halves of one contract -- markup written by `_article`, read by
    `collect()` -- and nothing in a browserless test suite executes the second half. A
    renamed class or a data attribute dropped on one side does not fail, it downloads
    the wrong thing, which is how this package has already shipped a broken Download
    button once. So every selector the script uses is asserted present here.
    """
    found = candidates.find([{"block_id": "p", "kind": "paragraph",
                              "text": "Deposited at GSE208532 in GEO."}])
    html = sheet.render([{"slug": "s", "doi": "10.1038/x", "candidates": found,
                          "verdict": {"state": readiness.READY}}])

    for hook in ['id="by"', 'id="progress"', 'id="out"',           # getElementById
                 "<article ", 'class="cand"',                      # querySelectorAll
                 'data-slug="', 'data-doi="',                      # node.dataset
                 'data-n="', 'data-acc="', 'data-repo="', 'data-level="',
                 'class="note"', 'class="missing"', 'class="complete"',
                 "const TOTAL ="]:
        assert hook in html, hook

    # The pairing most likely to drift: a radio group is found by the `data-n` of the
    # `.cand` that contains it, so the two numbers have to be the same one.
    ordinal = html.split('class="cand" data-n="')[1].split('"')[0]
    assert f'name="r-{ordinal}"' in html
    for role, _ in sheet._ROLES:
        assert f'value="{role}"' in html


def test_the_roles_the_sheet_offers_are_the_roles_scoring_understands():
    """A fourth radio nobody scores, or a renamed one, would come back in a label and
    be silently dropped by `own_set`."""
    assert {role for role, _ in sheet._ROLES} == truth.ROLES


# -- the label round trip ----------------------------------------------------

def _payload(**article):
    base = {"slug": "s", "doi": "10.1038/x", "aspect": "accessions",
            "labeled_by": "you@example.edu", "complete": True,
            "accessions": [], "missing": []}
    return {"aspect": "accessions", "labeled_by": "you@example.edu",
            "articles": [{**base, **article}]}


def _apply(tmp_path, payload, partial=False):
    source = tmp_path / "labels.json"
    source.write_text(json.dumps(payload))
    out = tmp_path / "truth"
    code = cli.main(["label", "--apply", str(source), "--truth", str(out)]
                    + (["--partial"] if partial else []))
    return code, out


def test_a_half_filled_label_is_refused(tmp_path):
    """A blank role scores as a deliberate `reused` call, quietly rewarding a model
    for the labeller's unfinished work."""
    code, out = _apply(tmp_path, _payload(accessions=[
        {"accession": "GSE1", "role": None, "note": ""}]))
    assert code == 2
    assert list(out.glob("*.json")) == []


def test_partial_writes_only_the_roles_that_were_given(tmp_path):
    code, out = _apply(tmp_path, _payload(accessions=[
        {"accession": "GSE1", "role": "own"},
        {"accession": "GSE2", "role": None}]), partial=True)
    assert code == 0
    record = json.loads((out / "s.json").read_text())
    assert [a["accession"] for a in record["accessions"]] == ["GSE1"]


def test_an_article_with_no_candidates_still_produces_a_usable_label(tmp_path):
    """This is the whole point of asking about the empty ones: `complete: true` over
    an empty list is a real claim, and it is what bounds recall."""
    code, out = _apply(tmp_path, _payload(accessions=[], complete=True))
    assert code == 0
    record = json.loads((out / "s.json").read_text())
    assert record["complete"] and record["accessions"] == []
    assert truth.own_set(record) == set()


def test_a_reported_finder_miss_is_recorded_as_a_pattern_bug(tmp_path):
    _, out = _apply(tmp_path, _payload(missing=["GSE999111"]))
    assert json.loads((out / "s.json").read_text())["finder_missed"] == ["GSE999111"]


def test_a_truth_file_is_byte_stable():
    record = {"slug": "s", "complete": True, "accessions": []}
    assert truth.dump(record) == truth.dump(dict(reversed(list(record.items()))))
