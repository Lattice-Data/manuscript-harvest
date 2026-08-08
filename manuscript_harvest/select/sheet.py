"""One self-contained HTML page for hand-labelling accession candidates.

Same shape and the same stdlib-only constraint as `extract/reviewsheet.py`: no Jinja,
no CDN, no server, opens by double-click, works offline. The differences are all
consequences of what is being labelled.

**It covers the whole corpus, not one article.** A review sheet is per-article
because its questions are about that article's bytes. Labelling is a sitting: 69
study-level candidates across 21 articles, plus 6 articles with none to confirm, is
one ~30-minute pass, and 27 separate files would turn it into 27 openings and 27
downloads.

**Sample-level candidates are shown but never asked about.** They are listed as a
count with the ids, because a curator may want to see them, and they carry no radios
because the set is not boundable -- see `candidates.SAMPLE`. Asking a human to
adjudicate 43 per-sample ids that came out of a truncated card enumeration would cost
an afternoon for an answer nobody wants.

**Every article gets a `complete` box, including the empty ones.** That checkbox is
the only thing that makes recall computable, and the articles where it matters most
are the ones with no candidates at all -- an empty result from a broken pattern and an
empty result from a paper that deposited nothing are the same file otherwise. The
free-text "missing" field beside it is where a regex gap gets reported, which makes
the labelling pass double as a test of `candidates._PATTERNS`.
"""

import html
import json
from typing import List, Optional, Sequence

from . import candidates, readiness

_STYLE = """
body { font: 14px/1.5 -apple-system, Segoe UI, sans-serif; margin: 0 auto;
       max-width: 62rem; padding: 1rem 1.5rem 8rem; color: #111; }
h1 { font-size: 1.3rem; margin-bottom: .25rem; }
h2 { font-size: 1.05rem; margin: 0 0 .2rem; }
article { border: 1px solid #ccc; border-radius: 6px; padding: .75rem 1rem;
          margin: 1.25rem 0; }
article.empty { background: #fcfcfa; }
.state { font: 11px/1 ui-monospace, monospace; text-transform: uppercase;
         letter-spacing: .06em; color: #666; }
.gaps { color: #8a5a00; font-size: 12px; margin: .2rem 0; }
.cand { border-top: 1px solid #eee; padding: .6rem 0 .4rem; }
.acc { font: 600 13px/1 ui-monospace, monospace; background: #eef3fb;
       padding: .15rem .35rem; border-radius: 3px; }
.repo { font-size: 11px; color: #555; margin-left: .4rem; }
blockquote { margin: .35rem 0 .35rem .2rem; padding: .3rem .6rem; color: #222;
             border-left: 3px solid #ddd; font-size: 13px; background: #fafafa; }
blockquote cite { display: block; font: 11px/1.4 ui-monospace, monospace;
                  color: #777; font-style: normal; margin-top: .2rem; }
label { margin-right: .9rem; } textarea { width: 100%; font: inherit; }
input[type=text] { font: inherit; padding: .2rem .4rem; width: 22rem; }
.samples { font: 11px/1.5 ui-monospace, monospace; color: #666; margin-top: .5rem; }
.note { width: 100%; }
#out { font: 11px/1.4 ui-monospace, monospace; height: 10rem; width: 100%;
       background: #fafafa; }
button { font: inherit; padding: .4rem .9rem; }
footer { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #ddd;
         padding: .75rem 0; }
.count { color: #555; }
"""

_SCRIPT = """
function collect() {
  const by = document.getElementById('by').value;
  const articles = [];
  document.querySelectorAll('article').forEach(function (node) {
    const accessions = [];
    node.querySelectorAll('.cand').forEach(function (row) {
      const picked = row.querySelector('input[name="r-' + row.dataset.n + '"]:checked');
      accessions.push({
        accession: row.dataset.acc,
        repository: row.dataset.repo,
        level: row.dataset.level,
        role: picked ? picked.value : null,
        note: row.querySelector('.note').value
      });
    });
    const missing = node.querySelector('.missing').value.trim();
    articles.push({
      slug: node.dataset.slug,
      doi: node.dataset.doi || null,
      aspect: 'accessions',
      labeled_by: by,
      complete: node.querySelector('.complete').checked,
      accessions: accessions,
      missing: missing ? missing.split(/[\\s,;]+/).filter(Boolean) : []
    });
  });
  const payload = {aspect: 'accessions', labeled_by: by, articles: articles};
  const done = articles.filter(function (a) { return a.complete; }).length;
  const roled = articles.reduce(function (n, a) {
    return n + a.accessions.filter(function (x) { return x.role; }).length; }, 0);
  document.getElementById('progress').textContent =
    roled + ' of ' + TOTAL + ' candidates labelled; ' +
    done + ' of ' + articles.length + ' articles marked complete';
  document.getElementById('out').value = JSON.stringify(payload, null, 2);
  return payload;
}
document.addEventListener('input', collect);
document.addEventListener('DOMContentLoaded', collect);
function download() {
  try {
    const text = JSON.stringify(collect(), null, 2);
    const blob = new Blob([text], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'accession-labels.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    document.getElementById('out').value = String(err);
  }
}
"""

#: The roles a labeller picks from, with the prompt each one answers. Closed set for
#: the same reason the review sheet uses radios: a free-text role would have to be
#: parsed, and "reanalysed" against "reused" is not a distinction anybody meant to
#: make.
_ROLES = (
    ("own", "this paper's own deposit"),
    ("reused", "someone else's data, reanalysed here"),
    ("not_an_accession", "not an accession at all (pattern bug)"),
)


def _json_for_script(value: dict) -> str:
    """JSON that survives being embedded in a ``<script>`` element.

    ``html.escape`` is wrong here and was a real bug in the review sheet: HTML5
    script data does not expand character references, so ``&quot;`` arrives at
    ``JSON.parse`` literally and the Download button throws. Escape only ``<``, which
    is the one thing that could terminate the element early.
    """
    return json.dumps(value, sort_keys=True).replace("<", "\\u003c")


def _mention(mention: dict) -> str:
    where = " · ".join(part for part in [
        mention.get("section") or "unlabelled section",
        mention.get("role"),
        mention.get("source_file"),
        mention.get("locator") or "",
    ] if part)
    return (f"<blockquote>{html.escape(mention.get('sentence') or '')}"
            f"<cite>{html.escape(where)}</cite></blockquote>")


def _candidate(index: int, candidate) -> str:
    # Two mentions is enough to judge a role and bounds the page: one article's
    # accession is mentioned in eight blocks, and eight repetitions of a near-identical
    # Methods sentence is scrolling, not evidence.
    shown = candidate.mentions[:2]
    extra = len(candidate.mentions) - len(shown)
    parts = [
        f'<div class="cand" data-n="{index}" '
        f'data-acc="{html.escape(candidate.accession)}" '
        f'data-repo="{html.escape(candidate.repository)}" '
        f'data-level="{html.escape(candidate.level)}">',
        f'<span class="acc">{html.escape(candidate.accession)}</span>'
        f'<span class="repo">{html.escape(candidate.repository)}'
        f' · mentioned {len(candidate.mentions)}x in '
        f'{html.escape(", ".join(candidate.sections))}</span>',
    ]
    parts += [_mention(m) for m in shown]
    if extra:
        parts.append(f'<div class="gaps">+{extra} further mention(s)</div>')
    parts.append("<div>" + "".join(
        f'<label><input type="radio" name="r-{index}" value="{value}"> {text}</label>'
        for value, text in _ROLES) + "</div>")
    parts.append('<div><input type="text" class="note" placeholder="note '
                 '(optional)"></div></div>')
    return "".join(parts)


def _article(counter: List[int], slug: str, doi: Optional[str], verdict: dict,
             found: Sequence) -> str:
    split = candidates.by_level(found)
    study, samples = split[candidates.STUDY], split[candidates.SAMPLE]
    parts = [
        f'<article class="{"empty" if not study else ""}" '
        f'data-slug="{html.escape(slug)}" data-doi="{html.escape(doi or "")}">',
        f'<div class="state">{html.escape(verdict.get("state") or "")}</div>',
        f"<h2>{html.escape(doi or slug)}</h2>",
    ]
    if verdict.get("gaps"):
        parts.append(f'<div class="gaps">gaps: '
                     f'{html.escape(", ".join(verdict["gaps"]))}</div>')
    if not study:
        parts.append('<div class="gaps">No study-level accession found. If this paper '
                     'did deposit data, name it below &mdash; that is a pattern bug '
                     'worth fixing.</div>')
    for candidate in study:
        parts.append(_candidate(counter[0], candidate))
        counter[0] += 1
    if samples:
        ids = ", ".join(c.accession for c in samples[:12])
        more = f" (+{len(samples) - 12} more)" if len(samples) > 12 else ""
        parts.append(f'<div class="samples">{len(samples)} sample-level id(s) also '
                     f'found, not asked about: {html.escape(ids)}{more}</div>')
    parts.append(
        '<p><label><input type="checkbox" class="complete"> '
        '<b>Complete</b> &mdash; every dataset this paper deposited is labelled '
        '<code>own</code> above (tick even when that is none of them)</label></p>'
        '<p>Accessions the finder missed, space or comma separated<br>'
        '<input type="text" class="missing" placeholder="e.g. GSE123456'
        '"></p></article>')
    return "".join(parts)


def render(articles: Sequence[dict]) -> str:
    """The whole labelling sheet, as one HTML string.

    `articles` is `[{slug, doi, verdict, candidates}]` in the order they should be
    read. The CLI builds that list; this function only lays it out, so the choice of
    which articles are worth labelling stays somewhere testable.
    """
    counter = [0]
    body = []
    total_study = 0
    for entry in articles:
        found = entry["candidates"]
        total_study += len(candidates.by_level(found)[candidates.STUDY])
        body.append(_article(counter, entry["slug"], entry.get("doi"),
                             entry.get("verdict") or {}, found))

    empties = sum(1 for e in articles
                  if not candidates.by_level(e["candidates"])[candidates.STUDY])
    return (
        '<!doctype html><meta charset="utf-8">'
        "<title>Accession labels</title>"
        f"<style>{_STYLE}</style>"
        f'<script type="application/json" id="meta">'
        f'{_json_for_script({"articles": len(articles), "candidates": total_study})}'
        "</script>"
        "<h1>Which accessions did each paper deposit?</h1>"
        f'<p class="count">{total_study} study-level candidate(s) across '
        f'{len(articles)} article(s); {empties} article(s) have none and need only '
        f'the <b>Complete</b> box. Only articles whose text '
        f'<code>readiness</code> says can be believed are here &mdash; an article '
        f'with no extractable body cannot be said to have deposited nothing.</p>'
        '<p class="count">For each candidate: is it <b>this paper\'s own deposit</b>, '
        'or <b>data it reanalysed</b> from somewhere else? The sentence it appeared '
        'in is quoted underneath. Then tick <b>Complete</b> so recall can be '
        'measured for that paper.</p>'
        + "".join(body)
        + '<footer><p>Who are you? <input type="text" id="by" '
          'placeholder="you@example.edu"> <span class="count" id="progress">'
          "</span></p>"
          '<textarea id="out" readonly></textarea>'
          '<p><button type="button" onclick="download()">Download the labels</button> '
          "then <code>manuscript-select label --apply accession-labels.json "
          "--truth truth/accessions</code> "
          "(if the file does not appear, copy the JSON from the box above)</p>"
          "</footer>"
        f"<script>const TOTAL = {total_study};{_SCRIPT}</script>"
    )


def articles_worth_labelling(entries: Sequence[dict]) -> List[dict]:
    """Drop the articles a label would mean nothing for.

    An article whose main text is a saved landing page has no body to have deposited
    anything in, so a `complete: true` on it would assert something the labeller has
    no way to know -- and would then be counted in recall as though they did. Nine of
    the 37 development-corpus directories are in that state, which is why this filter
    is here rather than left to whoever builds the list.
    """
    return [entry for entry in entries
            if readiness.trustworthy(entry.get("verdict") or {})]
