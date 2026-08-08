"""Score an answer against a hand-labelled one, per article and in aggregate.

This is the module that makes prompt work measurable instead of a matter of taste,
and it exists in this package rather than beside the prompts for one reason: it can
be tested without a model. The labels themselves are not here -- a truth file's
meaning depends on the question that produced it, so it travels with the aspect that
defines it and reaches this code through `--truth`. Same arrangement as
`manual_fetch.yaml`, whose spec is checked in while the bytes it describes are not.

**The `complete` flag is what makes recall a number.** A truth file listing one
deposit bounds precision immediately -- anything else predicted is wrong -- but it
says nothing about recall unless the labeller asserts they looked for others and
found none. Without that assertion a missed accession and an accession the labeller
never got to are the same file on disk. So recall is computed only over articles
whose label says `complete: true`, the rest are reported as `partial`, and the
headline says how many were excluded. This is the same rule as the fetch stage's
`none_listed` against `unknown_none_found`, applied to the gold standard.

**Why the baseline is in here.** `--baseline` scores "every study accession the
finder produced is a deposit", which is what a pipeline does when nobody adjudicates
the role. Keeping it in the tool means the number a change is compared against is
recomputed from the same truth on the same articles, rather than remembered from a
conversation.
"""

import json
from pathlib import Path
from typing import Iterable, Optional, Sequence

OWN = "own"
REUSED = "reused"
NOT_AN_ACCESSION = "not_an_accession"

ROLES = frozenset({OWN, REUSED, NOT_AN_ACCESSION})
"""What a label can say about a candidate.

`not_an_accession` is not redundant with simply leaving it out. It is the verdict
that a pattern matched something that is not an identifier at all, which is a bug
report about `candidates._PATTERNS` rather than a fact about the paper -- and one
that would otherwise be indistinguishable from a labeller's oversight.
"""


def read(path) -> Optional[dict]:
    """One truth file, or `None` when it is not there."""
    target = Path(path)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def read_dir(directory) -> dict:
    """`{slug: label}` for every `*.json` under a truth directory.

    Keyed by slug rather than DOI because that is what the corpus directories are
    named, and the alternative is normalising a DOI at every lookup.
    """
    root = Path(directory)
    if not root.is_dir():
        return {}
    labels = {}
    for path in sorted(root.glob("*.json")):
        label = read(path)
        if label:
            labels[label.get("slug") or path.stem] = label
    return labels


def own_set(record: Optional[dict]) -> set:
    """The `own` accessions in a truth file or an answer, as a set.

    Accepts both shapes on purpose -- a label and a prediction are the same shape, so
    the answer a pipeline writes can be diffed against a label without conversion, and
    a corrected label can be produced by editing a prediction.
    """
    if not record:
        return set()
    return {item["accession"] for item in (record.get("accessions") or [])
            if item.get("role") == OWN}


def score_one(predicted: Iterable[str], label: dict) -> dict:
    """Precision, recall and the confusions, for one article.

    Reported alongside the rates because the rates alone do not say what went wrong:
    `called_own_but_reused` climbing means the role judgement is too generous, which
    is the naive baseline's entire failure mode, while `missed` climbing means the
    finder or the judgement is losing real deposits. The two want opposite fixes.
    """
    predicted = set(predicted)
    truth = own_set(label)
    by_role = {item["accession"]: item.get("role")
               for item in (label.get("accessions") or [])}
    complete = bool(label.get("complete"))

    hits = predicted & truth
    false_positives = predicted - truth
    missed = truth - predicted

    return {
        "slug": label.get("slug"),
        "complete": complete,
        "predicted": sorted(predicted),
        "truth": sorted(truth),
        "hits": sorted(hits),
        "false_positives": sorted(false_positives),
        "missed": sorted(missed),
        "precision": (len(hits) / len(predicted)) if predicted else None,
        # Recall over an incomplete label would divide by a numerator the labeller
        # never claimed to have finished counting. `None` rather than 1.0, so an
        # unlabelled article cannot inflate an average by looking perfect.
        "recall": ((len(hits) / len(truth)) if truth else 1.0) if complete else None,
        "called_own_but_reused": sorted(a for a in false_positives
                                        if by_role.get(a) == REUSED),
        "called_own_but_not_an_accession": sorted(a for a in false_positives
                                                  if by_role.get(a) == NOT_AN_ACCESSION),
        "called_own_but_unlabelled": sorted(a for a in false_positives
                                            if a not in by_role),
    }


def score(predictions: dict, labels: dict) -> dict:
    """Aggregate over every article that has both a label and a prediction.

    Micro-averaged -- summing hits and predictions across articles before dividing --
    rather than averaging the per-article rates. A macro average lets an article with
    one accession weigh as much as one with ten, and 10.1016/j.isci.2023.106877 alone
    carries ten of the corpus's candidates.

    `per_article` is kept in the result and printed by the CLI, because a corpus-level
    number that improved while one article got worse is the regression this is meant
    to catch: the same reason `tests/expected_section_scores.json` holds a per-slug
    baseline instead of one figure.
    """
    rows = []
    for slug in sorted(labels):
        if slug not in predictions:
            continue
        rows.append(score_one(predictions[slug], labels[slug]))

    scored = [row for row in rows if row["complete"]]
    hits = sum(len(row["hits"]) for row in rows)
    predicted = sum(len(row["predicted"]) for row in rows)
    truth = sum(len(row["truth"]) for row in scored)
    recalled = sum(len(row["hits"]) for row in scored)

    return {
        "articles": len(rows),
        "articles_complete": len(scored),
        "articles_partial": len(rows) - len(scored),
        "labels_without_prediction": sorted(set(labels) - set(predictions)),
        "predictions_without_label": sorted(set(predictions) - set(labels)),
        "precision": (hits / predicted) if predicted else None,
        "recall": (recalled / truth) if truth else None,
        "predicted": predicted,
        "truth": truth,
        "hits": hits,
        "false_positives": predicted - hits,
        "confusions": {
            "called_own_but_reused":
                sum(len(row["called_own_but_reused"]) for row in rows),
            "called_own_but_not_an_accession":
                sum(len(row["called_own_but_not_an_accession"]) for row in rows),
            "called_own_but_unlabelled":
                sum(len(row["called_own_but_unlabelled"]) for row in rows),
        },
        "per_article": rows,
    }


def label_template(slug: str, doi: Optional[str],
                   found: Sequence, by: str = "") -> dict:
    """An unlabelled truth file for one article: every candidate, no role assigned.

    Written by the sheet so a labeller edits rather than authors, and so the file that
    comes back is the file the eval reads with no conversion step in between. `role`
    is `None` rather than a guess -- a pre-filled default is the thing most likely to
    be accepted without being read, and this file is the only thing measuring
    everything else.
    """
    return {
        "slug": slug,
        "doi": doi,
        "aspect": "accessions",
        "labeled_by": by,
        "complete": False,
        "accessions": [
            {"accession": candidate.accession, "repository": candidate.repository,
             "level": candidate.level, "role": None, "note": ""}
            for candidate in found
        ],
    }


def dump(record: dict) -> str:
    """A truth or answer file as text: sorted keys, trailing newline.

    Same stability contract as `blocks.jsonl`. A label is edited by hand and reviewed
    as a diff, and a formatter that reordered keys between writes would make every
    correction unreadable.
    """
    return json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True,
                      allow_nan=False) + "\n"
