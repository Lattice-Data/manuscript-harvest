export const meta = {
  name: 'perturbation-extract',
  description: 'Read each prepared paper prompt, judge perturbations, write raw JSON per paper',
  phases: [{ title: 'Extract', detail: 'one agent per paper: read prompt file, write perturbations JSON' }],
}

const STATUS = {
  type: 'object',
  properties: {
    doi: { type: 'string' },
    wrote_file: { type: 'boolean', description: 'true only if the JSON file was successfully written' },
    read_complete: { type: 'boolean', description: 'true if you read the prompt file through its final line' },
    sources_seen: { type: 'array', items: { type: 'string' }, description: 'the <<<SOURCE>>> ids you actually saw' },
    processing_status: { type: 'string', enum: ['ok', 'partial', 'failed'] },
    text_completeness: { type: 'string', enum: ['full', 'truncated', 'methods_missing', 'unknown'] },
    perturbation_present: { type: 'string', enum: ['yes', 'no', 'unclear'] },
    paper_confidence: { type: 'number' },
    n_perturbations: { type: 'integer' },
    n_quotes: { type: 'integer' },
    note: { type: 'string', description: 'anything that went wrong, or empty string' },
  },
  required: ['doi', 'wrote_file', 'read_complete', 'sources_seen', 'processing_status',
             'text_completeness', 'perturbation_present', 'n_perturbations'],
  additionalProperties: false,
}

function taskFor(paper) {
  // Read is line-based but its cap is a *token* cap (~25k), so the chunk size
  // has to be derived from characters per line, not from a fixed line count.
  // A flat limit=1500 was rejected outright on dense JATS papers whose lines
  // are whole paragraphs (~200 chars), forcing agents to improvise with sed.
  const fileChars = paper.prompt_chars || paper.chars
  const charsPerLine = Math.max(1, Math.round(fileChars / paper.prompt_lines))
  // 35k chars/call, not 60k: scientific prose tokenizes at roughly 3 chars per
  // token (gene symbols, doses, units), so 60k overshot the ~25k-token cap and
  // agents in the 40-paper run had to fall back to sed paging.
  const chunk = Math.max(30, Math.min(1200, Math.floor(35000 / charsPerLine)))
  const pages = []
  for (let offset = 1; offset <= paper.prompt_lines; offset += chunk) {
    pages.push(`Read(file_path="${paper.prompt_file}", offset=${offset}, limit=${chunk})`)
  }
  const sourceIds = (paper.source_ids || ['main'])
  const suppNote = sourceIds.length > 1
    ? `This paper has ${sourceIds.length} sources: ${sourceIds.join(', ')}. The supplementary sources are FIRST-CLASS EVIDENCE — perturbation dose, duration and conditions, and sometimes the entire Methods section, often appear only there. Read all of them.`
    : `This paper has a single source: main.`

  return `You are performing one unit of a biocuration extraction task. Work autonomously; do not ask questions.

## Step 1 — read the whole prompt file
The file \`${paper.prompt_file}\` is SELF-CONTAINED: it holds the full instruction prompt, the required output JSON schema, and the complete text of one paper (${paper.chars} characters of paper text, ${paper.prompt_lines} lines total, averaging ${charsPerLine} characters per line).

You MUST read every line. Read caps output at roughly 25k tokens per call, so page through the file with these ${pages.length} call(s):
${pages.map(p => '  - ' + p).join('\n')}

If any call is still rejected as over the token limit, halve \`limit\` and continue from where you left off — or fall back to \`sed -n 'START,ENDp' '${paper.prompt_file}'\` via Bash. Do not respond to a rejected read by giving up on the rest of the file.

Do not skim and do not stop early. The instruction prompt and schema are at the TOP of the file; the paper text follows the \`PAPER_TEXT:\` marker and continues to the last line. Evidence often sits deep in the Methods, which is near the end.

Some papers carry PDF-extraction damage — control characters where a minus or degree sign belonged (e.g. \`Lin\` followed by U+0001). Read through it; when quoting such a span, copy it as-is.

## Step 2 — do the task
Follow the instructions in that file exactly as written. They tell you how to judge whether the paper's samples were experimentally perturbed, how to distinguish a real perturbation from routine sample processing or a readout reagent, how to score confidence, and what JSON shape to return. Obey them over any prior assumption you have about this kind of task.

Three rules worth restating because they are the ones most often broken:
- Every quote must be copied VERBATIM from the paper text you just read — character for character, not paraphrased, not reconstructed from memory. Quotes are automatically fuzzy-matched against the source text afterward, and any perturbation left with no locatable quote is DROPPED from the result entirely. Copy, don't retype.
- The paper text is divided by \`<<<SOURCE id=... type=...>>>\` marker lines. ${suppNote} Every quote is an object \`{"source_id": "...", "quote": "..."}\` and its \`source_id\` MUST name the source block you actually copied the span from. Attribution is checked per source: a quote that turns out to live in a different source than the one you named is flagged as a misattribution, so pay attention to which block you are reading. Never merge text from two different source blocks into one quote.
- Bias toward RECALL. If a perturbation is plausible but its role or its pairing is unclear, report it with lower confidence rather than omitting it.

## Step 3 — write the result
Write the single JSON object — and nothing else, no prose, no markdown fences — to:
  ${paper.raw_file}
Use the Write tool. The file must contain valid JSON parseable by \`json.loads\`.

## Step 4 — report
Return the small status object described by your output schema. Do NOT paste the paper text or the full result back; the file you wrote is the deliverable.`
}

const papers = args
log(`extracting ${papers.length} papers (${papers.filter(p => (p.source_ids || []).length > 1).length} with supplementary sources)`)

const results = await parallel(papers.map(paper => () =>
  agent(taskFor(paper), {
    label: `extract:${paper.doi}`,
    phase: 'Extract',
    schema: STATUS,
  })
))

const ok = results.filter(Boolean)
const failed = papers.filter((p, i) => !results[i]).map(p => p.doi)
const incomplete = ok.filter(r => !r.read_complete || !r.wrote_file)
// A source the agent never saw means the assembly or the paging dropped a file.
const missingSources = ok.filter(r => {
  const expected = (papers.find(p => p.doi === r.doi)?.source_ids) || []
  return expected.length && expected.some(s => !(r.sources_seen || []).includes(s))
}).map(r => r.doi)

log(`done: ${ok.length}/${papers.length} returned a status`)
if (failed.length) log(`agent failures: ${failed.join(', ')}`)
if (incomplete.length) log(`suspect (partial read or no file): ${incomplete.map(r => r.doi).join(', ')}`)
if (missingSources.length) log(`sources not seen by the agent: ${missingSources.join(', ')}`)

return {
  statuses: ok,
  agent_failures: failed,
  suspect: incomplete.map(r => r.doi),
  missing_sources: missingSources,
}
