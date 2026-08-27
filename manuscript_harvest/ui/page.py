"""The panel: one HTML document, hand-written, no framework and no CDN.

Same choice and same reasons as `extract/reviewsheet.py`, which says it at length:
this repository has no web framework, and rendering a page with four cards and
polling two JSON endpoints does not justify introducing one. Everything here is
stdlib on the server and plain DOM on the client.

Two rules the client code follows throughout.

**Never `innerHTML` with data.** Every value on this page came from somewhere
uncontrolled -- a publisher's file name in a log line, a DOI from a pasted list, a
problem string naming a URL -- and the log pane in particular is a firehose of it.
Text goes in through `textContent`, always, so there is no path from a supplement's
name to script execution.

**The command is shown exactly as it will run.** Not a friendly paraphrase. It is
what a person would type, including the panel's own `--progress-jsonl` plumbing,
because a panel that displayed a command slightly unlike the one it ran would be
the first thing to mislead somebody debugging a run.
"""

import json

_STYLE = """
:root { --line: #d8d8d8; --ink: #111; --dim: #555; --faint: #777;
        --bg: #fff; --panel: #fafafa; --ok: #0f6e56; --warn: #854f0b;
        --bad: #a32d2d; --accent: #185fa5; }
* { box-sizing: border-box; }
body { font: 14px/1.5 -apple-system, "Segoe UI", sans-serif; margin: 0 auto;
       max-width: 62rem; padding: 1rem 1.5rem 4rem; color: var(--ink);
       background: var(--bg); }
h1 { font-size: 1.15rem; font-weight: 500; margin: 0; }
h2 { font-size: .95rem; font-weight: 500; margin: 0 0 .6rem; }
section { border: 1px solid var(--line); border-radius: 6px; padding: .8rem 1rem;
          margin: .75rem 0; }
header { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap;
         padding: .25rem 0 .5rem; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dim { color: var(--dim); } .faint { color: var(--faint); }
.small { font-size: 12px; }
.metrics { display: flex; gap: .5rem; flex-wrap: wrap; margin: 0 0 .5rem; }
.metric { flex: 1 1 12rem; background: var(--panel); border-radius: 6px;
          padding: .5rem .7rem; }
.metric .n { font-size: 1.3rem; font-weight: 500; }
.chips { display: flex; gap: .4rem; flex-wrap: wrap; margin: 0 0 .25rem; }
.chip { font-size: 12px; border: 1px solid var(--line); border-radius: 4px;
        padding: .2rem .5rem; }
.chip.ok { border-color: var(--ok); color: var(--ok); }
.chip.warn { border-color: var(--warn); color: var(--warn); }
.chip.bad { border-color: var(--bad); color: var(--bad); }
.row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.grow { flex: 1 1 16rem; }
button { font: inherit; padding: .35rem .8rem; border: 1px solid #888;
         border-radius: 4px; background: var(--bg); cursor: pointer; }
button:hover:not(:disabled) { background: #f2f2f2; }
button:disabled { color: #aaa; border-color: var(--line); cursor: default; }
button.primary { border-color: var(--accent); color: var(--accent); }
button.danger { border-color: var(--bad); color: var(--bad); }
select, input[type=text], textarea { font: inherit; padding: .3rem .4rem;
        border: 1px solid #999; border-radius: 4px; background: var(--bg);
        color: var(--ink); }
textarea { width: 100%; min-height: 4.5rem; font-family: ui-monospace, monospace;
           font-size: 12px; }
label.opt { font-size: 13px; color: var(--dim); margin-right: .9rem;
            white-space: nowrap; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; font-weight: 400; color: var(--dim); padding: .25rem .4rem; }
td { padding: .25rem .4rem; border-top: 1px solid #eee; }
td.mono { word-break: break-all; }
.state-new { color: var(--accent); } .state-refetch { color: var(--warn); }
.state-cached { color: var(--faint); }
.s-complete, .s-ok { color: var(--ok); }
.s-partial, .s-stale { color: var(--warn); }
.s-failed, .s-crashed { color: var(--bad); }
pre { background: #f6f6f6; border-radius: 4px; padding: .5rem .7rem; margin: .5rem 0 0;
      overflow: auto; max-height: 22rem; font: 11.5px/1.5 ui-monospace, monospace;
      white-space: pre-wrap; word-break: break-word; }
.cmd { background: var(--panel); border-radius: 4px; padding: .4rem .55rem;
       font: 11.5px/1.45 ui-monospace, monospace; color: var(--dim);
       overflow-x: auto; white-space: pre; margin: .5rem 0 0; }
.bar { height: 6px; background: #eee; border-radius: 3px; overflow: hidden;
       margin: .4rem 0; }
.bar > div { height: 100%; background: var(--accent); width: 0; transition: width .3s; }
.bar.idle > div { background: #bbb; }
details { margin: .75rem 0 0; }
summary { cursor: pointer; font-size: .95rem; color: var(--bad); }
.hidden { display: none; }
.err { color: var(--bad); font-size: 13px; }
.note { font-size: 12px; color: var(--dim); margin: .4rem 0 0; }
ul.plain { margin: .3rem 0 0; padding-left: 1.1rem; font-size: 12px; color: var(--dim); }
"""

_BODY = """
<header>
  <h1>manuscript-harvest</h1>
  <span class="mono small dim" id="where"></span>
  <span class="small faint" id="clock"></span>
</header>

<div class="metrics">
  <div class="metric"><div class="small dim">Corpus</div>
    <div class="n" id="m-papers">&mdash;</div>
    <div class="small faint mono" id="m-corpus-dir"></div></div>
  <div class="metric"><div class="small dim">Fetched</div>
    <div class="n" id="m-fetch">&mdash;</div>
    <div class="small faint" id="m-fetch-detail"></div></div>
  <div class="metric"><div class="small dim">Extracted</div>
    <div class="n" id="m-extract">&mdash;</div>
    <div class="small faint" id="m-extract-detail"></div></div>
</div>

<div class="chips" id="chips"></div>

<section>
  <h2>Fetch &mdash; DOI to pdf and supplements</h2>
  <div class="row">
    <select class="grow" id="doi-file"></select>
    <input type="file" id="doi-upload" accept=".txt,.dois,.doi,.list,text/plain">
  </div>
  <div id="paste-wrap" class="hidden" style="margin-top:.5rem">
    <textarea id="doi-text" placeholder="10.1038/s41586-021-03852-1&#10;one DOI per line; # starts a comment"></textarea>
  </div>
  <div class="row" style="margin-top:.5rem">
    <button id="btn-preflight">Check what this would do</button>
    <span class="small dim" id="preflight-summary"></span>
  </div>
  <div id="preflight" class="hidden" style="margin-top:.5rem"></div>
  <div class="row" style="margin-top:.6rem">
    <label class="opt"><input type="checkbox" id="opt-oa"> open access only</label>
    <label class="opt"><input type="checkbox" id="opt-nosuppl"> skip supplements</label>
    <label class="opt"><input type="checkbox" id="opt-force"> <span style="color:#a32d2d">force re-fetch</span></label>
  </div>
  <div class="row" style="margin-top:.5rem">
    <button class="primary" id="btn-fetch">Fetch</button>
    <button id="btn-login">Log in to proxy</button>
    <button id="btn-check">Check session</button>
    <span class="err" id="fetch-error"></span>
  </div>
</section>

<section>
  <h2>Extract &mdash; files to blocks.jsonl</h2>
  <div class="row">
    <button class="primary" id="btn-extract-all">Extract all</button>
    <input type="text" class="grow" id="extract-one" list="slugs"
           placeholder="one DOI or slug, then Extract one">
    <datalist id="slugs"></datalist>
    <button id="btn-extract-one">Extract one</button>
    <label class="opt"><input type="checkbox" id="opt-force-extract"> <span style="color:#a32d2d">force</span></label>
  </div>
  <p class="note">Unchanged articles are skipped from cache, so a re-run costs
     only what has moved. Forcing re-reads every file in the corpus.</p>
</section>

<section id="job-card" class="hidden">
  <h2><span id="job-label"></span></h2>
  <div class="row small dim">
    <span id="job-state"></span><span class="grow"></span><span id="job-timing"></span>
  </div>
  <div class="bar" id="job-bar"><div></div></div>
  <div class="row small dim">
    <span id="job-counts"></span><span class="grow"></span><span id="job-added"></span>
  </div>
  <div class="cmd" id="job-command"></div>
  <div class="row" style="margin-top:.5rem">
    <button class="danger" id="btn-stop">Stop after this item</button>
    <button class="danger hidden" id="btn-kill">Stop now</button>
    <button id="btn-copy">Copy log</button>
    <label class="opt"><input type="checkbox" id="opt-chain"> extract when the fetch finishes</label>
  </div>
  <div id="job-recent"></div>
  <pre id="job-log"></pre>
</section>

<section>
  <h2>Recently added</h2>
  <div id="recent"></div>
</section>

<details>
  <summary>Housekeeping that deletes or rewrites what is stored</summary>
  <p class="note">Each of these previews first. Applying needs the word
     <span class="mono">delete</span> typed below, and cannot be undone.</p>
  <div class="row" style="margin-top:.5rem">
    <input type="text" id="confirm" placeholder="type delete to arm apply"
           style="width:14rem">
    <span class="small dim" id="confirm-state">apply is disarmed</span>
  </div>
  <table style="margin-top:.5rem">
    <tbody id="danger-rows"></tbody>
  </table>
</details>

<p class="note" id="footer"></p>
"""

_SCRIPT = r"""
const TOKEN = __TOKEN__;
let cursor = 0, jobId = null, lastState = null;

const $ = (id) => document.getElementById(id);
const text = (node, value) => { node.textContent = value == null ? "" : String(value); };

function api(path, body) {
  const options = { headers: { "X-Harvest-Token": TOKEN } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(function (response) {
    return response.json().then(function (data) {
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    });
  });
}

function bytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return (i === 0 ? v : v.toFixed(1)) + " " + units[i];
}

function duration(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  return m + "m " + String(s % 60).padStart(2, "0") + "s";
}

function counts(map) {
  return Object.keys(map || {}).sort().map((k) => k + "=" + map[k]).join("  ");
}

function chip(parent, label, level) {
  const span = document.createElement("span");
  span.className = "chip" + (level ? " " + level : "");
  text(span, label);
  parent.appendChild(span);
}

function statusClass(status) {
  return status ? "s-" + String(status).replace(/[^a-z_]/gi, "") : "";
}

function cell(row, value, className) {
  const td = document.createElement("td");
  if (className) td.className = className;
  text(td, value);
  row.appendChild(td);
  return td;
}

// -- state -----------------------------------------------------------------

function renderState(state) {
  lastState = state;
  const corpus = state.corpus, health = state.health;
  text($("where"), "127.0.0.1:" + state.port + "  ·  " + state.root);
  text($("m-papers"), corpus.papers + " papers");
  text($("m-corpus-dir"), corpus.corpus_dir + (corpus.exists ? "" : " (missing)"));
  const fetched = corpus.fetch || {}, extracted = corpus.extract || {};
  text($("m-fetch"), (fetched.complete || 0) + " complete");
  text($("m-fetch-detail"), counts(fetched) + "  ·  " + bytes(corpus.bytes));
  text($("m-extract"), (extracted.complete || 0) + " complete");
  const totals = corpus.totals || {};
  text($("m-extract-detail"), counts(extracted) + "  ·  " +
       (totals.blocks || 0).toLocaleString() + " blocks, " +
       (totals.tables || 0).toLocaleString() + " tables");

  const chips = $("chips");
  chips.replaceChildren();
  chip(chips, health.config_found ? "config.yaml: " + health.config_path
                                  : "no config file: built-in defaults",
       health.config_found ? "" : "warn");
  if (health.proxy_tier) {
    if (health.session_saved === null) chip(chips, "proxy session: unknown", "warn");
    else if (!health.session_saved) chip(chips, "no proxy session — log in before paywalled papers", "bad");
    else chip(chips, "proxy session saved " + duration(health.session_age_seconds) + " ago", "ok");
  } else {
    chip(chips, "proxy tier off");
  }
  chip(chips, health.elsevier_key
        ? "Elsevier key set (" + health.elsevier_key_source + ")"
        : "no Elsevier key — Cell Press supplements unreachable",
       health.elsevier_key ? "ok" : "warn");
  if (!health.contact_email) chip(chips, "no contact_email set", "warn");
  if (state.last_check) {
    chip(chips, "session check " + (state.last_check.returncode === 0 ? "passed" : "failed")
         + " at " + state.last_check.finished_at.slice(11, 19) + "Z",
         state.last_check.returncode === 0 ? "ok" : "bad");
  }

  const picker = $("doi-file");
  const chosen = picker.value;
  picker.replaceChildren();
  (state.doi_files || []).forEach(function (file) {
    const option = document.createElement("option");
    option.value = "file:" + file.name;
    text(option, file.name + " — " + file.dois + " DOIs");
    picker.appendChild(option);
  });
  const pasted = document.createElement("option");
  pasted.value = "paste";
  text(pasted, "paste DOIs instead…");
  picker.appendChild(pasted);
  if (chosen) picker.value = chosen;
  if (!picker.value) picker.value = "paste";
  onSourceChange();

  const slugs = $("slugs");
  slugs.replaceChildren();
  (corpus.recent || []).forEach(function (article) {
    const option = document.createElement("option");
    option.value = article.slug;
    slugs.appendChild(option);
  });

  renderRecent(corpus.recent || []);
  renderDanger();
  text($("footer"), "Reading " + corpus.files.toLocaleString() + " files in " +
       corpus.corpus_dir + ". Nothing on this page runs until you press it.");
  setBusy(state.busy);
}

function renderRecent(articles) {
  const host = $("recent");
  host.replaceChildren();
  if (!articles.length) {
    const p = document.createElement("p");
    p.className = "note";
    text(p, "No articles in the corpus yet.");
    host.appendChild(p);
    return;
  }
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["DOI", "fetched", "fetch", "extract", "files", "size"].forEach(function (label) {
    const th = document.createElement("th");
    text(th, label);
    head.appendChild(th);
  });
  table.appendChild(head);
  articles.forEach(function (article) {
    const row = document.createElement("tr");
    cell(row, article.doi || article.slug, "mono");
    cell(row, (article.fetched_at || "").slice(0, 16).replace("T", " "), "small faint");
    cell(row, article.fetch_status, statusClass(article.fetch_status));
    cell(row, article.extract_status || "not extracted",
         statusClass(article.extract_status));
    cell(row, article.files);
    cell(row, bytes(article.bytes));
    table.appendChild(row);
  });
  host.appendChild(table);
}

// -- fetch source ----------------------------------------------------------

function onSourceChange() {
  const pasting = $("doi-file").value === "paste";
  $("paste-wrap").classList.toggle("hidden", !pasting);
}

function source() {
  const chosen = $("doi-file").value;
  if (chosen && chosen.indexOf("file:") === 0) {
    return { source: "file", name: chosen.slice(5) };
  }
  return { source: "text", text: $("doi-text").value };
}

function preflight() {
  text($("fetch-error"), "");
  api("/api/preflight", source()).then(renderPreflight).catch(function (e) {
    text($("fetch-error"), e.message);
  });
}

function renderPreflight(data) {
  const host = $("preflight");
  host.replaceChildren();
  host.classList.remove("hidden");
  const c = data.counts;
  text($("preflight-summary"),
       c.total + " DOIs: " + c.new + " new, " + c.refetch + " re-fetched, " +
       c.cached + " already complete");

  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["DOI", "in corpus", "supplements", "extract", "a plain run would"].forEach(
    function (label) {
      const th = document.createElement("th");
      text(th, label);
      head.appendChild(th);
    });
  table.appendChild(head);
  const verdict = {
    "new": "fetch it",
    "refetch": "fetch it again",
    "cached": "skip it — needs force",
  };
  data.rows.forEach(function (row) {
    const tr = document.createElement("tr");
    cell(tr, row.doi, "mono");
    cell(tr, row.fetch_status || "—", statusClass(row.fetch_status));
    cell(tr, row.supplementary_status || "—", "small");
    cell(tr, row.extract_status || "—", statusClass(row.extract_status));
    cell(tr, verdict[row.state], "state-" + row.state);
    table.appendChild(tr);
  });
  host.appendChild(table);

  if (data.repeated.length || data.unparseable.length || data.counts.truncated) {
    const list = document.createElement("ul");
    list.className = "plain";
    const say = function (message) {
      const li = document.createElement("li");
      text(li, message);
      list.appendChild(li);
    };
    if (data.repeated.length)
      say(data.repeated.length + " DOI(s) listed more than once, counted once: " +
          data.repeated.slice(0, 5).join(", "));
    data.unparseable.forEach(function (line) { say("not a DOI, skipped: " + line); });
    if (data.counts.truncated)
      say(data.counts.truncated + " more DOIs not shown; the run would still fetch them");
    host.appendChild(list);
  }
}

// -- running ---------------------------------------------------------------

function setBusy(busy) {
  if (lastState) lastState.busy = busy;
  ["btn-fetch", "btn-login", "btn-check", "btn-extract-all", "btn-extract-one"]
    .forEach(function (id) { $(id).disabled = busy; });
  document.querySelectorAll("button.danger-run").forEach(function (button) {
    button.disabled = busy;
  });
  // Last, because it is the one that decides whether Apply may be enabled: a job
  // finishing must not re-arm a button the confirmation box no longer authorises.
  onConfirmChange();
}

function run(kind, options, extra) {
  text($("fetch-error"), "");
  const body = Object.assign({ kind: kind, options: options || {} }, extra || {});
  return api("/api/run", body).then(function (data) {
    cursor = 0;
    jobId = data.job.id;
    $("job-log").textContent = "";
    renderJob(data.job);
    setBusy(true);
    pollJob();
  }).catch(function (e) { text($("fetch-error"), e.message); });
}

function startFetch() {
  run("fetch", {
    oa_only: $("opt-oa").checked,
    no_supplements: $("opt-nosuppl").checked,
    force: $("opt-force").checked,
  }, source());
}

function renderJob(job) {
  const card = $("job-card");
  card.classList.remove("hidden");
  text($("job-label"), job.label + (job.live ? " — running" : " — finished"));
  const progress = job.progress || {};
  const total = progress.total, done = progress.done || 0;

  let state = job.live ? (job.stopping ? "stopping after the item in flight" : "running")
                       : (job.error ? "could not start" : "exit " + job.returncode);
  if (!job.live && job.returncode === 130) state = "stopped at your request (exit 130)";
  text($("job-state"), state);
  text($("job-timing"), duration(job.elapsed) + " elapsed" +
       (job.eta ? "  ·  ~" + duration(job.eta) + " left (rough)" : ""));

  const bar = $("job-bar");
  bar.classList.toggle("idle", !total);
  bar.firstElementChild.style.width = total ? (100 * done / total) + "%" : "100%";
  text($("job-counts"), total ? done + " of " + total + "   " + counts(progress.by_status)
                             : counts(progress.by_status));
  // What a fetch adds is files and bytes; what an extract produces is blocks and
  // tables. Each stage leaves the other's fields out of its heartbeat, so showing
  // both pairs would print a zero that reads as "nothing happened".
  const added = [];
  if (job.kind === "fetch") {
    if (progress.files) added.push("+" + progress.files + " files");
    if (progress.bytes) added.push("+" + bytes(progress.bytes));
  } else {
    if (progress.blocks) added.push(progress.blocks.toLocaleString() + " blocks");
    if (progress.tables) added.push(progress.tables.toLocaleString() + " tables");
  }
  text($("job-added"), added.join("  ·  "));
  text($("job-command"), job.command);

  $("btn-stop").classList.toggle("hidden", !job.live);
  $("btn-kill").classList.toggle("hidden", !(job.live && job.stopping));
  renderJobRecent(progress.recent || []);
}

function renderJobRecent(items) {
  const host = $("job-recent");
  host.replaceChildren();
  if (!items.length) return;
  const table = document.createElement("table");
  table.style.marginTop = ".5rem";
  items.slice().reverse().forEach(function (item) {
    const row = document.createElement("tr");
    cell(row, item.doi || item.slug, "mono");
    cell(row, item.status + (item.cached ? " (cached)" : ""), statusClass(item.status));
    if (item.files != null && item.bytes != null) {
      cell(row, item.files + " files", "small");
      cell(row, bytes(item.bytes), "small faint");
    } else {
      cell(row, (item.blocks || 0) + " blocks", "small");
      cell(row, (item.tables || 0) + " tables", "small faint");
    }
    cell(row, (item.problems || []).join("; "), "small faint");
    table.appendChild(row);
  });
  host.appendChild(table);
}

function pollJob() {
  api("/api/job?cursor=" + cursor).then(function (data) {
    if (!data.job) { setBusy(false); return; }
    renderJob(data.job);
    const log = data.job.log;
    if (log.dropped) {
      $("job-log").textContent += "... " + log.dropped + " earlier lines dropped ...\n";
    }
    if (log.lines.length) {
      const pane = $("job-log");
      const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 24;
      pane.textContent += log.lines.join("\n") + "\n";
      if (atBottom) pane.scrollTop = pane.scrollHeight;
    }
    cursor = log.cursor;
    if (data.job.live) {
      setTimeout(pollJob, 750);
      return;
    }
    setBusy(false);
    refresh();
    // Read now rather than when the fetch started: the checkbox lives in the job
    // card, which only appears once a job is running, so a value captured at the
    // start would have ignored every use of it.
    if ($("opt-chain").checked && data.job.kind === "fetch"
        && data.job.returncode !== 130) {
      $("opt-chain").checked = false;
      run("extract-all", { force: false });
    }
  }).catch(function () { setTimeout(pollJob, 2000); });
}

// -- housekeeping ----------------------------------------------------------

const DANGER = [
  ["revalidate", "revalidate", "re-check that each stored full text is the paper its DOI asked for"],
  ["drop-media", "drop media", "delete stored images, audio and video no text comes out of"],
  ["drop-orphans", "drop orphans", "delete stored files no manifest entry points at"],
  ["prune", "prune", "evict oldest articles until the corpus fits its budget"],
];

function renderDanger() {
  const host = $("danger-rows");
  if (host.childElementCount) return;
  DANGER.forEach(function (entry) {
    const row = document.createElement("tr");
    const label = document.createElement("td");
    const name = document.createElement("div");
    name.className = "mono";
    text(name, entry[1]);
    const why = document.createElement("div");
    why.className = "small faint";
    text(why, entry[2]);
    label.appendChild(name);
    label.appendChild(why);
    row.appendChild(label);

    const actions = document.createElement("td");
    actions.style.textAlign = "right";
    const preview = document.createElement("button");
    preview.className = "danger-run";
    text(preview, "Preview");
    preview.onclick = function () { run(entry[0], { apply: false }); };
    const apply = document.createElement("button");
    apply.className = "danger danger-run danger-apply";
    apply.disabled = true;
    text(apply, "Apply");
    apply.onclick = function () {
      run(entry[0], { apply: true }, { confirm: $("confirm").value });
    };
    actions.appendChild(preview);
    actions.appendChild(document.createTextNode(" "));
    actions.appendChild(apply);
    row.appendChild(actions);
    host.appendChild(row);
  });
}

function onConfirmChange() {
  const armed = $("confirm").value.trim() === "delete";
  document.querySelectorAll("button.danger-apply").forEach(function (button) {
    button.disabled = !armed || (lastState && lastState.busy);
  });
  text($("confirm-state"), armed ? "apply is armed" : "apply is disarmed");
}

// -- wiring ----------------------------------------------------------------

function refresh() {
  return api("/api/state").then(renderState).catch(function () {});
}

$("doi-file").onchange = onSourceChange;
$("btn-preflight").onclick = preflight;
$("btn-fetch").onclick = startFetch;
$("btn-login").onclick = function () { run("login", {}); };
$("btn-check").onclick = function () { run("check", {}); };
$("btn-extract-all").onclick = function () {
  run("extract-all", { force: $("opt-force-extract").checked });
};
$("btn-extract-one").onclick = function () {
  run("extract-one", { force: $("opt-force-extract").checked },
      { target: $("extract-one").value });
};
$("btn-stop").onclick = function () { api("/api/stop", { force: false }); };
$("btn-kill").onclick = function () {
  if (window.confirm("Kill it now? A fetch stopped this way can leave downloaded " +
                     "files with no manifest entry, which drop-orphans clears up."))
    api("/api/stop", { force: true });
};
$("btn-copy").onclick = function () {
  navigator.clipboard.writeText($("job-log").textContent);
};
$("confirm").oninput = onConfirmChange;
$("doi-upload").onchange = function (event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function () {
    $("doi-file").value = "paste";
    onSourceChange();
    $("doi-text").value = String(reader.result);
    preflight();
  };
  reader.readAsText(file);
};

// The token arrives in the URL because the panel prints one line to a terminal and
// a person pastes it. Taken out of the address bar immediately so it does not sit
// in browser history or get read over a shoulder; the copy in this closure is what
// every request uses from here on.
if (window.location.search) {
  window.history.replaceState({}, "", window.location.pathname);
}

refresh().then(function () {
  if (lastState && lastState.busy) { cursor = 0; pollJob(); }
});
setInterval(function () {
  if (!lastState || !lastState.busy) refresh();
  text($("clock"), new Date().toLocaleTimeString());
}, 5000);
text($("clock"), new Date().toLocaleTimeString());
onConfirmChange();
"""


def render(token: str) -> str:
    """The whole page, with the session token baked into its script."""
    script = _SCRIPT.replace("__TOKEN__", json.dumps(token))
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>manuscript-harvest</title>\n"
        f"<style>{_STYLE}</style></head>\n"
        f"<body>{_BODY}\n<script>{script}</script>\n</body></html>\n"
    )
