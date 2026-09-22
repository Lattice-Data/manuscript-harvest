"""Score the Stage B entry-condition acceptance runs (v0.0.25).

**Lives at the skill root, NOT in `harness/`** -- `tests/test_seam.py` asserts the
harness names no task word in code, and this file is hardcoded DOIs and
`perturbation_present`. Same placement and same reason as
`score-acceptance-v0023.py`.

    python score-acceptance-stageb.py [--r1 DIR --r2 DIR] [--baseline DIR]

**The blocking criterion moved at v0.0.25, and the old one is kept for
continuity.** Through v0.0.24 the cap read `(processing_status,
text_completeness)`, so the blocker was whether those two agreed between runs:
27/30 at v0.0.23, 20/24 at v0.0.24. The determination no longer reads them.
**What blocks now is whether `stage_b_capped` itself agrees**, because the cap is
keyed on a quote the harness verified rather than on a self-report -- and that is
the claim this version makes. Both numbers are printed.

The v0.0.24 results this file scored are written up in `ACCEPTANCE-v0.0.24.md`;
the expectations below are v0.0.25's.

The rest guard the ways this change could go wrong rather than right:

  2  the cap still holds where the HARNESS can prove truncation (rung 3). Those
     two papers must stay capped whatever the model reports -- if telling the
     model about the truncation talks it out of reporting one, `harness.validate`'s
     override is the backstop and must be seen to fire.
  3  no non-negative is ever capped. Exercised by one paper here rather than by
     the four degraded-text positives a longer draft carried: that property is a
     code invariant, and the scorer says NOT EXERCISED rather than PASS if even
     that one paper does not reach a non-negative Stage A.
  4  Stage A does not move. v0.0.24 touches Step 0 only, so a Stage A flip means
     the assembly block leaked into the criteria -- the v0.0.10 attractor shape.
  5  the false-positive anchors stay `no` and stay uncapped.
  6  every determination that differs from the baseline is classified.

**Criterion 6 and the baseline gap.** The stored corpus is v0.0.22 and no
v0.0.23 corpus run exists, so against the default baseline a `no` -> `yes` move
on these papers could be v0.0.23's infection rule rather than anything here.
That is stated loudly rather than silently absorbed; pass `--baseline` a
v0.0.23 run over this same set to make criterion 6 clean.
"""
import argparse
import json
from pathlib import Path

RUN_ROOT = Path.home() / ".manuscript-harvest" / "perturbation"

#: Defect kinds the harness cannot confirm by quote. `no_methods_content` is an
#: absence, so `task.rules` falsifies it against the manifest's `section_chars`
#: instead -- which means it is the one kind that keeps working when the quote
#: verifier does not. Named here so criterion 1's exercise count can exclude it.
UNQUOTABLE_KINDS = {"no_methods_content"}

# The 15 capped papers. Value is the strict expectation where there is one, or
# None where BOTH outcomes are legitimate: the cap holding ("unclear") and the
# cap releasing ("no", because the defect was the pipeline's own cut) are both
# correct answers to different texts, and predicting which per paper would be
# inventing a claim. What is scored on these is stability, the absence of a
# "yes", and -- when capped -- that the record says why.
CAPPED = {
    # rung 3: the harness truncated these itself, so the cap does not depend on
    # the model's opinion at all. `harness.validate` forces "truncated" when the model
    # says "full" on a harness-truncated text; these two are where that fires.
    "10.1126_sciimmunol.adz8650": "unclear",
    "10.3390_genes15030298": "unclear",
    # v0.0.25's sharpest prediction. Its defect is three garbled runs in a
    # SUPPLEMENTARY source, and the scope rule says that cannot have hidden a
    # pairing sentence -- so the cap releases and the paper lands on curator
    # ruling 6's own answer, which the old trigger overrode to "unclear".
    "10.1126_science.aat1699": "no",
    # Everything else: either outcome, stability enforced.
    "10.1101_2021.09.16.460628": None,
    "10.1038_s41586-020-2157-4": None,
    "10.1038_s41586-022-04817-8": None,
    "10.1038_s41586-023-06981-x": None,
    "10.1038_s41591-021-01586-1": None,
    "10.1038_s41598-022-17832-6": None,
    "10.1101_2020.03.04.976407": None,
    "10.1101_2023.03.06.531398": None,
    "10.1101_2024.09.05.611379": None,
    "10.1101_2024.10.18.618987": None,
    "10.1126_science.abo1984": None,
    "10.1161_atvbaha.122.317953": None,
}

# paper -> (expected determination, group, note)
#
# The four degraded-text positives, the review and `j.ccell.2023.08.015` were in
# a 30-paper draft and are deliberately NOT here. They existed to show the cap
# never reaches a non-negative, which `task.rules.stage_b` guarantees by only
# ever rewriting a "no" and `tests/test_harness_guards.py` asserts over every
# Stage A value. See `criterion 3` below for what their absence costs.
EXPECT = {
    # C: harness facts look degraded, the model was right that they are not
    "10.1126_science.abo7257": ("no", "anchor", "section coverage 0.00"),
    "10.1096_fj.202300601rrr": ("no", "anchor", "section coverage 0.23"),
    "10.1126_science.aat5031": ("no", "anchor", "coverage 1.00, no methods label"),
    "10.1038_s41467-020-19737-2": ("no", "anchor", "210 control chars/10k"),
    "10.1126_science.abf3041": ("no", "anchor", "165 control chars/10k"),
    "10.1038_s41586-021-04345-x": ("no", "anchor", "65 control chars/10k"),
    # D: the three that flipped their self-report between two v0.0.23 runs
    "10.1182_bloodadvances.2023011445": ("no", "flipper",
                                         "full->truncated moved the determination"),
    "10.1016_j.healun.2026.02.1666": ("no", "flipper", "two routes to one cap"),
    "10.1038_s41586-021-03852-1": ("unclear", "flipper",
                                   "unclear via A5, NOT degraded_text"),
}

for pid, want in CAPPED.items():
    EXPECT[pid] = (want, "capped", "either outcome" if want is None else
                   "HARNESS truncated: cap must hold")


def load(run: Path, pid: str):
    for sub in ("validated", "raw"):
        f = run / sub / (pid + ".json")
        if f.is_file():
            try:
                return json.loads(f.read_text())
            except ValueError:
                return None
    return None


def quality(rec):
    return (rec.get("processing_status"), rec.get("text_completeness"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--r1", type=Path, default=RUN_ROOT / "work-accept-stageb-r1")
    ap.add_argument("--r2", type=Path, default=RUN_ROOT / "work-accept-stageb-r2")
    ap.add_argument("--baseline", type=Path,
                    default=RUN_ROOT / "work-corpus-v0022-revalidate",
                    help="a run to classify movement against; the default is "
                         "v0.0.22 and PREDATES v0.0.23's criteria changes")
    args = ap.parse_args()
    runs = (args.r1, args.r2)

    rows, pending = [], []
    for pid, (exp, group, note) in EXPECT.items():
        recs = [load(run, pid) for run in runs]
        if not all(recs):
            pending.append(pid)
        base = load(args.baseline, pid)
        rows.append((pid, exp, group, note, recs, base))

    hdr = (f"{'paper':33} {'grp':9} {'base':9} {'want':15} "
           f"{'r1 det':9} {'r2 det':9} {'r1 quality':24} {'r2 quality':24} verdict")
    print(hdr)
    print("=" * len(hdr))

    quality_flips, stage_a_flips, det_unstable, _cap_flips = [], [], [], []
    defect_driven: list = []
    wrong, cap_on_positive, harness_cap_lost = [], [], []

    for pid, exp, group, note, recs, base in rows:
        if not all(recs):
            print(f"{pid:33} {group:9} {'?':9} {str(exp):15} "
                  f"{'-':9} {'-':9} {'-':24} {'-':24} PENDING")
            continue
        r1, r2 = recs
        d1, d2 = r1.get("perturbation_present"), r2.get("perturbation_present")
        q1, q2 = quality(r1), quality(r2)
        v1, v2 = r1.get("validation") or {}, r2.get("validation") or {}
        a1, a2 = v1.get("stage_a"), v2.get("stage_a")

        if q1 != q2:
            quality_flips.append((pid, q1, q2))
        c1, c2 = bool(v1.get("stage_b_capped")), bool(v2.get("stage_b_capped"))
        if c1 != c2:
            _cap_flips.append((pid, c1, c2))
        # Did the NEW half of the trigger actually fire here?
        #
        # Two exclusions, and the second one is the whole point. A cap carried by
        # `harness_withheld` would have fired under v0.0.24 too, so it says
        # nothing about v0.0.25. And a cap carried by `no_methods_content` says
        # nothing either: that kind is FALSIFIED against `section_chars`, never
        # quote-checked, so it is the one defect that survives a broken quote
        # path untouched -- which is exactly what happened. Under the v0.0.25 bug
        # all 14 quotable claims were dropped, `2021.09.16.460628` capped on its
        # methods claim alone, and a counter that accepted it reported
        # "exercised by 1" over a dead verifier. Only a QUOTE-VERIFIED defect
        # proves the path is alive.
        #
        # Counted across both runs: the question is whether the mechanism ran at
        # all, not whether it ran twice.
        def _quote_verified(rec):
            return any(e.get("kind") not in UNQUOTABLE_KINDS
                       for e in (rec.get("text_defects") or [])
                       if isinstance(e, dict))

        if ((c1 and not r1.get("harness_withheld") and _quote_verified(r1))
                or (c2 and not r2.get("harness_withheld") and _quote_verified(r2))):
            defect_driven.append(pid)
        if a1 != a2:
            stage_a_flips.append((pid, a1, a2))

        verdict = []
        if d1 != d2:
            det_unstable.append((pid, d1, d2))
            verdict.append("UNSTABLE")
        if exp is not None and d1 != exp:
            wrong.append((pid, group, exp, d1))
            verdict.append("FAIL")
        if group == "capped" and exp is None and d1 == "yes":
            wrong.append((pid, group, "no or unclear", d1))
            verdict.append("FAIL(yes)")
        # The cap is asymmetric by construction. A capped positive means Stage B
        # stopped expressing the rule it exists for.
        for v, d in ((v1, d1), (v2, d2)):
            if v.get("stage_b_capped") and v.get("stage_a") in ("yes", "unclear",
                                                                "not_applicable"):
                cap_on_positive.append((pid, v.get("stage_a")))
                verdict.append("CAP-ON-NONNEGATIVE")
        # Where the harness itself truncated, the cap must not depend on the
        # model's opinion: `harness.validate` overrides "full" to "truncated".
        if exp == "unclear" and group == "capped":
            for i, v in enumerate((v1, v2), 1):
                if not v.get("stage_b_capped"):
                    harness_cap_lost.append((pid, f"r{i}"))
                    verdict.append("HARNESS-CAP-LOST")
        if not verdict:
            verdict.append("PASS")
        before = base.get("perturbation_present") if base else "?"
        print(f"{pid:33} {group:9} {str(before):9} {str(exp):15} "
              f"{str(d1):9} {str(d2):9} {str(q1):24} {str(q2):24} "
              f"{','.join(dict.fromkeys(verdict))}")

    scored = len(EXPECT) - len(pending)
    print(f"\n{scored}/{len(EXPECT)} papers have results in BOTH runs")

    print("\n=== GATE ===")
    # An empty failure list over unscored papers is not a pass. Refuse first --
    # the shape `score-acceptance-v0023.py` had to be taught as well.
    if pending:
        print(f"  INCOMPLETE: {len(pending)} of {len(EXPECT)} papers have no result "
              f"in both runs. The criteria below are NOT evaluated.")
        for pid in pending:
            print(f"    pending: {pid}")
        return 1

    # How many papers could actually TRIP criterion 3. With the degraded-text
    # positives out of the set, "no non-negative was capped" is held by one paper
    # -- and a criterion that prints PASS over zero applicable papers is the
    # vacuous-pass shape, so the count is printed beside the verdict rather than
    # left to be assumed.
    non_negative = sum(
        1 for pid, exp, group, note, recs, base in rows if all(recs)
        for v in ((recs[0].get("validation") or {}),)
        if v.get("stage_a") in ("yes", "unclear", "not_applicable"))

    cap_flips = [(pid, a, b) for pid, a, b in _cap_flips]
    # Agreement over a mechanism that never ran is not evidence about the
    # mechanism. At v0.0.25 that was not hypothetical: `validate_defects` read a
    # key `verify_quote_sourced` does not return, so all 14 quotable defect
    # claims in run 1 were dropped and the cap fell back to `harness_withheld`
    # and `no_methods_content`, both deterministic. Criterion 1 would then have
    # agreed 24/24 and certified a dead gate. So the blocker now carries the
    # count of caps the NEW trigger actually decided, and zero is a failure
    # rather than a remark -- which is where it differs from criterion 3, whose
    # property is independently held by `task.rules.stage_b`.
    #
    # The bar is >= 1 deliberately. Run 1 of v0.0.25, re-validated after the fix,
    # had 4 caps decided by a quote-verified defect, out of 13 verified defects
    # across 10 papers; a
    # higher threshold would be a number chosen rather than measured, and could
    # fail a set that is legitimately clean.
    criteria = (
        ("1. stage_b_capped agrees across runs (BLOCKER)",
         cap_flips, len(set(defect_driven)), True),
        ("1b. self-report agrees (continuity, not a gate)", quality_flips, None, False),
        ("2. harness-proved cap still holds", harness_cap_lost, None, False),
        ("3. no non-negative was capped", cap_on_positive, non_negative, False),
        ("4. stage_a did not move", stage_a_flips, None, False),
        ("5. expectations met", wrong, None, False),
        ("6. determination stable across runs", det_unstable, None, False),
    )
    failed = False
    for label, items, exercised, blocking in criteria:
        if items:
            verdict = "FAIL: " + str(items)
            failed = True
        elif exercised == 0 and blocking:
            verdict = ("FAIL: NOT EXERCISED -- no cap in either run was decided "
                       "by a verified text_defects entry, so this run says "
                       "nothing about the trigger v0.0.25 introduced. Agreement "
                       "here would be agreement over harness facts alone")
            failed = True
        elif exercised == 0:
            verdict = ("NOT EXERCISED -- no paper in this set reached a "
                       "non-negative Stage A, so this is not a pass. Held by "
                       "task.rules.stage_b and tests/test_harness_guards.py")
        elif exercised is not None:
            verdict = f"PASS (exercised by {exercised} paper(s))"
        else:
            verdict = "PASS"
        print(f"  {label:44} {verdict}")

    print(f"\n  CAP stability           {scored - len(cap_flips)}/{scored}"
          f"   <- what v0.0.25 claims to fix")
    print(f"  self-report stability   {scored - len(quality_flips)}/{scored}"
          f"   (27/30 at v0.0.23, 20/24 at v0.0.24; no longer determinative)")
    print(f"  stage_a stability       {scored - len(stage_a_flips)}/{scored}")

    # What the change was FOR, reported as a number rather than an impression.
    released = [pid for pid, exp, group, note, recs, base in rows
                if group == "capped" and all(recs)
                and not (recs[0].get("validation") or {}).get("stage_b_capped")]
    print(f"\n  caps released           {len(released)}/{len(CAPPED)}"
          f"{'' if not released else ': ' + ', '.join(released)}")
    print("  A released cap is the predicted effect, not a regression: the text "
          "was complete and the\n  reported defect was the pipeline's own cut. "
          "Read the ones that did NOT release -- those\n  are the texts where "
          "something is genuinely missing.")

    moved = [(pid, (base.get("perturbation_present") if base else "?"),
              recs[0].get("perturbation_present"))
             for pid, exp, group, note, recs, base in rows
             if all(recs) and base
             and base.get("perturbation_present") != recs[0].get("perturbation_present")]
    print(f"\n  moved vs baseline       {len(moved)}")
    for pid, before, after in moved:
        print(f"    {pid:33} {before} -> {after}")
    if moved:
        print(f"  Baseline is {args.baseline.name}. If that is a v0.0.22 run it "
              f"PREDATES v0.0.23's\n  criteria changes, so a `no` -> `yes` here "
              f"may be the infection rule and not this version.\n  Pass "
              f"--baseline a v0.0.23 run over this same set to attribute these.")

    # A gate that prints FAIL and exits 0 is the vacuous pass one layer up.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
