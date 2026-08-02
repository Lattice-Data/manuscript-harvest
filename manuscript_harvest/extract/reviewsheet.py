"""One self-contained HTML page: the questions, the evidence, and a JSON answer.

Stdlib only -- `html.escape` and f-strings. No Jinja, no CDN, no framework, no
server. The sheet opens by double-click, works offline, and is e-mailable.

Why HTML and not a terminal walk or a CSV round-trip:

- `cmd_show --full` already renders a card to a terminal, so the terminal option
  is cheaper than it looks -- but half of this corpus's 1,327 table-card lines
  exceed 100 characters and the longest is 742, and a curator in a terminal has
  no way to open `supplementary/06_mmc3.xlsx` in Excel next to the question.
- CSV puts a multi-line monospaced card inside one cell, which Excel renders as
  an unreadable single line, and makes every correction free text.

The HTML sheet gives side-by-side source access through a `file://` link and
closed-set radios instead of free text. What comes back out is exactly the
`reviews/<slug>.json` shape `review.py` reads.
"""

import html
import json
from pathlib import Path
from typing import List, Optional

from . import review

_STYLE = """
body { font: 14px/1.5 -apple-system, Segoe UI, sans-serif; margin: 0 auto;
       max-width: 60rem; padding: 1rem 1.5rem 6rem; color: #111; }
h1 { font-size: 1.3rem; } h2 { font-size: 1rem; margin: 0 0 .25rem; }
section { border: 1px solid #d8d8d8; border-radius: 6px; padding: .75rem 1rem;
          margin: 1rem 0; }
section.signoff { border-color: #444; }
.kind { font: 11px/1 ui-monospace, monospace; text-transform: uppercase;
        letter-spacing: .06em; color: #666; }
.why { color: #555; margin: .25rem 0 .5rem; }
pre { background: #f6f6f6; border-radius: 4px; padding: .6rem .8rem; overflow-x: auto;
      font: 12px/1.45 ui-monospace, monospace; max-height: 26rem; }
code { background: #f0f0f0; padding: .1rem .3rem; border-radius: 3px; }
label { margin-right: 1rem; } textarea { width: 100%; font: inherit; }
input[type=text] { font: inherit; padding: .2rem .4rem; }
#out { font: 11px/1.4 ui-monospace, monospace; height: 12rem; background: #fafafa; }
button { font: inherit; padding: .4rem .9rem; }
footer { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #ddd;
         padding: .75rem 0; }
"""

_SCRIPT = """
function collect() {
  const by = document.getElementById('by').value;
  const at = new Date().toISOString();
  const answers = [];
  document.querySelectorAll('section.item').forEach(function (node) {
    const verdict = node.querySelector('input[name="v-' + node.dataset.n + '"]:checked');
    const note = node.querySelector('.note').value;
    const field = node.querySelector('.override');
    const evidence = node.querySelector('.evidence');
    if (!verdict && !note && !(field && field.value) && !(evidence && evidence.checked))
      return;
    const override = {};
    if (field && field.value) {
      if (field.dataset.as === 'row') {
        override.header_row = field.value.trim().toLowerCase() === 'none'
          ? null : parseInt(field.value, 10);
      } else if (field.dataset.as === 'yesno') {
        override.has_content = field.value.trim().toLowerCase().startsWith('y');
      } else {
        override.label = field.value;
      }
    }
    if (evidence && evidence.checked) override.evidence = false;
    answers.push({
      kind: node.dataset.kind, key: JSON.parse(node.dataset.key),
      source_sha256: node.dataset.sha || null,
      card_fingerprint: node.dataset.fingerprint || null,
      verdict: verdict ? verdict.value : 'cannot_tell',
      override: Object.keys(override).length ? override : null,
      note: note, by: by, at: at
    });
  });
  const signed = document.querySelector('input[name="signoff"]:checked');
  const payload = JSON.parse(document.getElementById('base').textContent);
  payload.answers = answers;
  payload.sign_off = signed
    ? {verdict: signed.value, by: by, at: at,
       note: document.getElementById('signoff-note').value}
    : null;
  document.getElementById('out').value = JSON.stringify(payload, null, 2);
  return payload;
}
document.addEventListener('input', collect);
document.addEventListener('DOMContentLoaded', collect);
function download() {
  const payload = collect();
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'review-' + payload.slug + '.json';
  a.click();
}
"""

#: The kind-specific input, keyed by item kind: (label, placeholder, parse mode).
_FIELDS = {
    review.TABLE_HEADER: ("Header row, counting from 1 the way the "
                          "<code>Shape:</code> line above does &mdash; or the "
                          "word <code>none</code>",
                          "e.g. 4, or none", "row"),
    review.FILE_HAS_CONTENT: ("Does it carry article evidence? (yes / no)",
                              "yes or no", "yesno"),
    review.SUPPLEMENT_LABEL: ("The publisher's name for this file",
                              "e.g. Supplementary Table 3", "label"),
    review.MAIN_TEXT_PRESENT: ("Which rendition is the article? (jats / pdf)",
                               "jats or pdf", "label"),
    review.SECTION_SPAN: ("Which section does the unlabelled text belong to?",
                          "e.g. methods", "label"),
}


def _link(article_dir: Optional[Path], relative: str) -> str:
    if not article_dir or not relative:
        return ""
    target = (Path(article_dir) / relative).resolve()
    return (f' <a href="file://{html.escape(str(target))}">open the file</a>'
            if target.exists() else "")


def _item_section(index: int, item: dict, article_dir: Optional[Path],
                  previous: Optional[dict]) -> str:
    key = item["key"]
    path = key.get("path") or key.get("source_file") or ""
    field = _FIELDS.get(item["kind"])
    parts = [
        f'<section class="item" data-n="{index}" data-kind="{html.escape(item["kind"])}"',
        f' data-key=\'{html.escape(json.dumps(key, sort_keys=True))}\'',
        f' data-sha="{html.escape(item.get("source_sha256") or "")}"',
        f' data-fingerprint="{html.escape(item.get("card_fingerprint") or "")}">',
        f'<div class="kind">{html.escape(item["kind"])}</div>',
        f'<h2>{html.escape(item["question"])}</h2>',
        f'<div class="why">{html.escape(item.get("why") or "")}</div>',
        f'<div><code>{html.escape(path)}</code>'
        f'{" <code>" + html.escape(key["locator"]) + "</code>" if key.get("locator") else ""}'
        f'{_link(article_dir, path)}</div>',
    ]
    if item.get("body"):
        parts.append(f"<pre>{html.escape(item['body'])}</pre>")
    if previous:
        parts.append(f'<div class="why">previously answered '
                     f'<b>{html.escape(previous.get("verdict") or "")}</b> by '
                     f'{html.escape(previous.get("by") or "")} on '
                     f'{html.escape(previous.get("at") or "")} -- re-queued because '
                     f'{html.escape(previous.get("why") or "the source changed")}</div>')
    parts.append("<div>" + "".join(
        f'<label><input type="radio" name="v-{index}" value="{verdict}"> '
        f'{verdict.replace("_", " ")}</label>' for verdict in sorted(review.VERDICTS))
        + "</div>")
    if field:
        caption, placeholder, mode = field
        parts.append(f'<p>{caption}<br><input type="text" class="override" '
                     f'data-as="{mode}" placeholder="{html.escape(placeholder)}"></p>')
    if item["kind"] in {review.SUPPLEMENT_LABEL, review.FILE_HAS_CONTENT}:
        parts.append('<p><label><input type="checkbox" class="evidence"> '
                     'this file is not article evidence (peer review, reporting '
                     'summary, description of files)</label></p>')
    parts.append('<p>Note<br><textarea class="note" rows="2"></textarea></p></section>')
    return "".join(parts)


def render(extraction: dict, queue: List[dict], existing: Optional[dict] = None,
           article_dir=None, stale: Optional[List[dict]] = None) -> str:
    """The whole review sheet for one article, as one HTML string."""
    base = existing or review.empty_review(extraction)
    base = {**base, "answers": [],
            "signed_manifest_sha256": extraction.get("source_manifest_sha256")}
    by_key = {review.answer_key(a["kind"], a["key"]): a for a in (stale or [])}

    body = [
        f"<h1>Review: {html.escape(str(extraction.get('doi')))}</h1>",
        f'<div class="why">{html.escape(str(extraction.get("status")))} &middot; '
        f'{len([i for i in queue if i["kind"] != review.SIGN_OFF])} question(s) '
        f'&middot; blocks {extraction.get("totals", {}).get("blocks", 0)}</div>',
    ]
    for index, item in enumerate(queue):
        if item["kind"] == review.SIGN_OFF:
            continue
        body.append(_item_section(index, item, article_dir,
                                  by_key.get(review.answer_key(item["kind"],
                                                               item["key"]))))

    body.append(
        '<section class="signoff"><div class="kind">sign_off</div>'
        '<h2>Is this extraction fit to answer curation questions from?</h2>'
        '<div>'
        + "".join(f'<label><input type="radio" name="signoff" value="{value}"> '
                  f'{label}</label>'
                  for value, label in (("fit", "fit"),
                                       ("fit_with_notes", "fit with notes"),
                                       ("unfit", "unfit")))
        + '</div><p>Note<br><textarea id="signoff-note" rows="2"></textarea></p>'
          '</section>')

    return (
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>Review {html.escape(str(extraction.get('slug')))}</title>"
        f"<style>{_STYLE}</style>"
        f'<script type="application/json" id="base">'
        f"{html.escape(json.dumps(base, sort_keys=True))}</script>"
        + "".join(body)
        + '<footer><p>Who are you? <input type="text" id="by" '
          'placeholder="you@example.edu"></p>'
          '<textarea id="out" readonly></textarea>'
          '<p><button onclick="download()">Download the answers</button> '
          'then <code>manuscript-extract review &lt;doi&gt; --apply '
          'review-&lt;slug&gt;.json</code></p></footer>'
        f"<script>{_SCRIPT}</script>"
    )
