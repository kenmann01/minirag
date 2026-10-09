# The Map Writes the Test: the evaluation harness handbook

A study guide for the evaluation harness. It teaches the mental model first, then
anchors every concept to the code that implements it, so the architecture you
designed and the implementation that exists can be read as one thing.

Citations look like (tasks.py:135) and mean `app/tasks.py` line 135 on branch
`feat/eval-harness`. Every citation in this document was checked against the
branch at commit 30ec7fa. Entry points are given as `python -m app <command>`.

---

## 1. The one thesis

Proficiency lives in the context, not in the model (docs/prd.md:63). That single
sentence is the claim the whole harness exists to defend, and the harness does
not defend it by arguing. It defends it by measurement: one agent, one task
list, three context levels, one scoreboard.

The scoreboard is the evidence. It reports tasks passed, tool calls, and cost
per run (score.py:243-252). The same eight questions are asked three times:

- RUN 1, context `bare`: the agent gets only the question.
- RUN 2, context `map`: the agent also gets the repository map.
- RUN 3, context `map_rules`: the agent also gets retrieved industry rules.

The three arms are fixed in one tuple (score.py:29-33). If the score climbs
monotonically across the arms while tool calls and cost fall, the thesis is
proven for this repository and this corpus, with numbers anyone can re-run.
The measured gap between the runs is the evidence (docs/prd.md:63).

One more property makes the comparison honest, and it is worth dwelling on.
The model never changes across the arms. The harness builds the jailed agent
once and reuses that one instance in all three runs (score.py:450-454), on the
same pinned model with the same seed (agent.py:64-68). Only the context inside
the prompt differs per arm (score.py:489-498). One controlled variable, one
measured variable: that is what makes the gap attributable to context rather
than to luck.

Read everything else in this handbook as machinery in service of that one
measurement.

## 2. The cast and what is under test

The system has two input lanes and one measurement spine (docs/prd.md:106).

**The map lane** reads a repository and produces the map: module-level mermaid
diagrams for humans and a graphify `graph.json` for agents (docs/prd.md:106).
The graph is inside-the-repo knowledge. Its facts are exact: this class calls
that class, in this file (graph.py:41-66 loads it; graph.py:162-184 renders it
as mermaid). Questions built from it are graded by an exact substring check on
symbol and file name (score.py:151-157).

**The corpus lane** converts public industry documents into markdown, ingests
them into Postgres as `policy_chunks` and `sections`, and serves them through a
retrieve-only bridge (docs/prd.md:106; bridge.py:22-46). It is outside-the-repo
knowledge: prose rules, banking regulation, aviation manuals. Questions built
from it are graded by a model judge plus a citation check (score.py:92-97).

**The subject under test is the agent.** Not the repository, not the RAG
pipeline. The system "does not evaluate the software under test. It evaluates
the agent that works on the software" (docs/prd.md:73). Keep that sentence
nearby; half of the naming confusion below comes from forgetting it.

The two lanes side by side:

| Lane | Knowledge | Lives in | Graded by |
|---|---|---|---|
| map | inside the repo, exact | graphify `graph.json` (graph.py:40-65) | script substring check (score.py:143-170) |
| corpus | outside the repo, prose | `policy_chunks` in Postgres (bridge.py:22-46) | judge plus citation check (score.py:173-197) |

### The direction rule

The owner once had this backwards, so it gets its own box.

Graph node labels are the RAG's query strings. Never the reverse. The generator
takes the source node of each chosen edge, reads its human label, and fires
that label at the corpus as a retrieval query (tasks.py:252-257, with the label
lookup at graph.py:104-109). The RAG never sees code: the bridge embeds the label
text and searches the corpus table with it (bridge.py:38-39; retrieve.py:79-121).
No source file, class body, or edge structure ever enters the vector store, and
the corpus never picks graph edges. The map aims the questions; the corpus
fleshes them out.

### Two exams, two subjects

There are two exam runners in this repo and they test different subjects.

This is old, and not part of eval harness. The Minion golden exam,
`python -m app eval`, loads eval/goldens.json and runs each question through
the same search the ask path uses. Search reads policy_chunks. It runs ten
fixed, hand-written cases (evaluate.py:16 loads eval/goldens.json; the ten
cases ship in the file) through the exact ask path, checks recall and required
facts, and exits nonzero if any case fails (evaluate.py:73-119; cli.py:224-237).
Its subject: retrieval plus generation, the pipeline.

The **new harness**, `python -m app score`, tests the agent. It derives a fresh
task list from the map, runs a jailed repo agent on it three times, and writes
the scoreboard (cli.py:69-77; score.py:365-414). Its subject: the agent working
a repository it has never seen.

Same repo, two exams, different defendants.

## 3. Why Fineract

The demo needs a stand-in client: a real codebase, big enough to be credible,
small enough to map on stage, in a domain that pairs with a public rulebook.
Apache Fineract, the open-source banking core, is the pick. The gate run
measured it at full scope: 21.697 seconds wall time, 61,983 nodes, 303,539
edges (docs/prd.md:299-302; the recorded gate document lives at
eval/fineract-gate.json). Large but mappable, and banking pairs naturally with
the reg-z-1026 corpus that anchors tier 2 (docs/prd.md:231).

What breaks without Fineract? Nothing in code. The task generator runs on any
graph that holds at least four EXTRACTED call or import edges whose source,
relation, and source file occur once (tasks.py:235-236; graph.py:82-101), and
the gate's loans-coverage check is just a scan for the package name
(graph.py:187-191). What dies is everything the demo says:

- Tier-1 tasks at real scale: the claim "the map works on a real repository,
  not a toy" (docs/prd.md:69) needs a repository that is actually real.
- RUN 2: the map arm is assembled from graph nodes, edges, and the module
  mermaid diagram (tasks.py:342-353). No map, no second arm, no gap to measure.
- The talk's title. The five gate numbers are the scale slide (docs/prd.md:293),
  and "The Map Writes the Test" is a claim about maps of real repositories.

The gate also stress-tested the human-facing half of the map at scale. At
61,983 nodes, module-level mermaid is the only rendering that survives: the
largest module offered 9,255 candidate nodes and the diagram cap is 24, and
the whole-repository diagram is out of the question (docs/prd.md:302). That is
why the map excerpt shown to the agent in RUN 2 draws one module's diagram,
not the repository's (tasks.py:342-353; graph.py:162-184).

Fineract is swappable by design. The corpus is already a folder; the repository
is one `--repo` argument away (cli.py:70-72). The bank is a prop with measured
dimensions, chosen so the prop and the rulebook rhyme.

## 4. The join: a self-writing exam

Here is the core trick. Neither lane contains a single question. The graph
holds structure, the corpus holds prose, and no human wrote an exam. The
question is manufactured at the join, and it is born holding its own answer
key.

Walk the join once. The generator picks an edge, say our canonical worked
example, `LoanProductHandler calls LoanProduct`. It takes the source node's
label, `LoanProductHandler`, and queries the corpus with it (tasks.py:254-257).
The bridge returns prose chunks about loan product rules (bridge.py:22-46). The
script writes each tier-1 question from the edge: the source, the source file,
and whether the edge calls or imports (tasks.py:84-89). It then hands the model
one prompt containing the fixed facts, with the instruction "Phrase tier-2
questions. Do not invent facts" (tasks.py:160-167). One chat call produces the
wording of the four tier-2 questions (tasks.py:274-275).

Born holding its own answer key means this:

- A tier-1 question is that template. Its expected value is the edge payload
  itself, taken from the graph (tasks.py:278-288). The graded answer is the
  graph target in its source file. A test keeps that target when a fake model
  invents a different one (tests/test_tasks.py:131).
- A tier-2 task's expected value is the one shown chunk id. The model echoes
  the fact number and the generator joins on that number (tasks.py:190-206,
  tasks.py:289-299). A citation other than that shown chunk fails the whole
  run with `TaskGenerationError` (tasks.py:296-299; the CLI turns that into
  exit code 1, cli.py:190). The docstring says it plainly: "A tier-2 citation
  that is not the shown bridge chunk fails the whole list" (tasks.py:220).

This is answerability by construction: a task exists only if the context to
answer it demonstrably exists. Three payoffs follow.

**Payoff one: no cherry-picking.** Nobody chose the facts. Edge selection is a
deterministic sort over EXTRACTED call and import edges, loan paths first
(graph.py:82-101). The docstring is blunt: "A human does not choose the facts"
(graph.py:87). The objection "you picked easy questions" has no surface to
attach to.

**Payoff two: relevance filtering. The map is a lens.** Only EXTRACTED edges
whose relation is a call or import survive selection (graph.py:9 and 89-93);
INFERRED edges never become questions, and the committed fixture graph carries
one precisely so the tests can prove the generator ignores it
(docs/demo-rig.md:83-88). The lens also points the corpus: swap the banking
folder for the aviation folder and the tier-2 questions change flavor with no
code change (tests/test_tasks.py:169-178).

**Payoff three: an attributable scoreboard.** Because the arms differ only in
context, each gap has one owner. The RUN 1 to RUN 2 gap is the value of code
knowledge (the map). The RUN 2 to RUN 3 gap is the value of domain knowledge
(the corpus). The arms are declared once, in order (score.py:29-33), and that
declaration is what makes the scoreboard an argument rather than a number.

## 5. The analogy

Keep this one. It compresses the whole design into a trade everyone knows.

An apprentice electrician arrives at a building he has never seen. The
**blueprint** on the wall is the graph: what connects to what, exact and
complete (graph.py:40-65). The **code book** on the shelf is the corpus: the
written rules of the trade, prose and authoritative (bridge.py:22-46). The
**inspector** writes the exam, and being a professional, writes each question
with the cited code section already stapled to it: that is the tier-2 task
carrying its shown bridge chunk id (tasks.py:311-325). The **apprentice**
is the jailed agent, allowed to walk the building and read plans but never to
step outside it (agent.py:22-31). The **master electrician** grades: he checks
the apprentice's answer against the cited section and fails it if the citation
does not hold (score.py:100-140, 173-197).

Every role in the story has a module. When a design question comes up, ask it
in the analogy first: the inspector does not teach, he tests; the master does
not choose the sections, the inspector stapled them; the apprentice who
wanders burns daylight. Then translate back to code.

| Story role | Code | Where |
|---|---|---|
| blueprint | the graphify graph | graph.py:40-65 |
| code book | the corpus, served retrieve-only | bridge.py:22-46 |
| inspector | the task generator | tasks.py:209-339 |
| question with the cited section stapled on | tier-2 task with its chunk id | tasks.py:311-325 |
| apprentice | the jailed agent | agent.py:58-125 |
| master electrician | judge plus citation check | score.py:100-140, 173-197 |

## 6. Anatomy of one score run

One command starts it all:

```
python -m app score --repo PATH --graph PATH --output PATH
```

(cli.py:69-77; dispatch at cli.py:168-198.) Here is the run, end to end, in
data-flow order.

**Step 1: load the map, pick the edges.** The generator loads nodes and edges
from the graphify file (tasks.py:233; graph.py:41-66). Edge selection is fully
deterministic: keep EXTRACTED call and import edges whose source, relation, and
source file occur once, sort loan-first then by source, target, relation, and
take four (graph.py:82-101; the count is `TASK_COUNT = 4` at tasks.py:13). A
question whose wording already contains the callee is skipped (tasks.py:92-100).
Fewer than four and the run refuses to start (tasks.py:235-236). Our canonical
edge `LoanProductHandler calls LoanProduct` is one of the four.

**Step 2: labels through the bridge.** For each chosen edge, the source node's
label becomes a corpus query (tasks.py:254-257). The bridge is retrieve-only:
it embeds the label, fuses a dense vector lane with a keyword lane using
reciprocal rank fusion with k equal to 60 (retrieve.py:59-76), and returns
chunk records with ids, titles, text, and distance (bridge.py:22-46). Every
returned chunk id is echoed to stderr so a retrieval miss stays visible
(bridge.py:40-41), and the reranker is deliberately bypassed because it
deduplicates by section and would silently drop chunks the log must keep
(bridge.py:26-27). The corpus here is the banking corpus, so the
`LoanProductHandler` label pulls back reg-z lending chunks.

**Step 3: the script writes tier 1, and the model phrases tier 2.** Tier-1
prompts come from the edge template (tasks.py:84-89). One prompt assembles the
fixed facts and the tier-2 phrasing contract (tasks.py:160-167). The model's
entire contribution is one JSON object of tier-2 question wording, each item
carrying its fact number (tasks.py:274-275). It cannot invent evidence.
Tier-1 expected answers come from the edges (tasks.py:278-288), and tier-2
citations must be that fact's shown bridge chunk (tasks.py:296-299). The
generator also records each tier-2 task's grounding distance: the mean cosine
distance of the chunk it cites (tasks.py:303-309), which the board later shows
per task.

**Step 4: eight cases, one dataset.** The task list becomes a pydantic-evals
Dataset of eight Cases, tier-1 cases carrying the script evaluator, tier-2
cases carrying the judge plus the citation check, and the scoreboard report
attached at the dataset level (score.py:272-298). The list is also written to
disk as `tasks.json` so every later number is auditable (score.py:436-439).

**Step 5: three context arms.** The same dataset is evaluated three times
(score.py:466-531). The prompt given to the agent per case is assembled by arm
(score.py:489-498): `bare` is the question alone; `map` adds the map excerpt,
which is the case's edge, its file, and the module mermaid diagram
(tasks.py:342-353); `map_rules` adds the full text of the cited chunks on top
(score.py:266-269). Before the RUN 3 arm starts, the bridge is called again for
every tier-2 task so that arm's own retrieval appears in the log
(score.py:468-484). One uuid covers all three arms as one run identity
(score.py:464).

**Step 6: the jailed agent answers.** The agent is a pydantic-ai agent on
local Ollama with exactly two tools, `list_dir` and `read_file` (agent.py:58-125).
Both tools resolve paths through one jail function that rejects anything
outside the repository root with a `PermissionError` (agent.py:22-31). The
agent is capped at 12 tool calls with a request limit one higher (agent.py:10
and 134-138); past the cap the attempt fails closed with an empty answer
instead of limping on (agent.py:140-141). Temperature 0, seed 77
(agent.py:67).

**Step 7: the judge and the citation verifier grade.** Tier 1 is a pure script:
the answer must contain the expected target symbol and the file name
(score.py:143-170). Tier 2 gets two graders. The `LLMJudge` runs the rubric
"the answer is supported by one of the allowed chunk ids" on local Ollama
(score.py:23-27 and 77-97). Then a second agent returns a structured verdict
naming exactly one supporting chunk id (score.py:100-140), and `RuleCitation`
passes the case only when that verdict is true and the cited id is on the
case's allowed list (score.py:173-197). The judge's tokens are billed into the
run like any other model call (score.py:181-182).

**Step 8: scoreboard, reports, sink, board.** `ScoreboardReport` folds the
eight case results into one row: passes total and per tier, tool calls, prompt
and completion tokens, and the cost figure from the local token-price formula
(score.py:200-263; formula at score.py:68-74). The row's public columns are
run id, context, tasks passed, tasks total, tool calls, and cost
(score.py:245-252). Each arm's full report is written as JSON (`bare.json`,
`map.json`, `map_rules.json`, score.py:340-362 and 526), and each row is
pushed into the metrics sink, which writes `runs`, `task_results` (including
grounding distance), and `tool_calls` rows in Postgres (score.py:527-530;
metrics.py:157-205). The Grafana board reads those tables and refreshes every
5 seconds (docs/demo-rig.md:45-47). A compare page can put the three reports
side by side (cli.py:78-81).

### One task, traced end to end

Follow one tier-2 task, our canonical edge, through the whole machine. The
chunk ids below are illustrative shapes; the format is the bridge contract
(docs/prd.md:193).

1. Selection keeps the edge `LoanProductHandler calls LoanProduct` (graph.py:82-101).
2. The label `LoanProductHandler` goes to the bridge; reg-z chunks come back,
   say `lending-rules:s12:c01` and `lending-rules:s15:c02` (tasks.py:254-257;
   bridge.py:22-46).
3. The model phrases the tier-2 question: "Which lending rule limits what a
   loan product handler may charge at product open?" citing
   `lending-rules:s12:c01` (tasks.py:160-167).
4. The gate check: the cited id is that fact's shown chunk, so the task
   survives. Had the model cited `made-up-id`, the run would die here
   (tasks.py:296-299).
5. The task becomes Case `t2-01`, its cited chunks and edge stored as metadata
   and origin (score.py:283-292).
6. In RUN 1 the agent sees only the question. In RUN 3 it also sees the full
   text of the cited chunks appended to the prompt (score.py:489-498), which
   is the whole point of the arm.
7. The judge must name an allowed id and `RuleCitation` checks it
   (score.py:173-197). A pass lands in `passed_by_tier["2"]`
   (score.py:208-219), in a `task_results` row (metrics.py:74-77), and in the
   family-d panel on the board.

Its tier-1 sibling uses the same edge differently: the question is the
template, and the expected answer is the graph's own target, `LoanProduct` in
its source file (tasks.py:84-89, tasks.py:278-288), graded by substring, no
model involved (score.py:151-157).

That is one run: map, bridge, phrasing, dataset, three arms, jail, judge,
board. Everything else in this handbook is commentary on these eight steps.

## 7. Pydantic Evals in one page

If you have never used pydantic-evals, it has five ideas and this harness uses
all five. The imports sit at the top of score.py:11-14.

**A Case is one exam item.** It carries `inputs` (what the task function
receives), `metadata` (the answer key and the origin), and its own list of
evaluators. Here each case's inputs are the task id, prompt, and tier; its
metadata carries `expected` and `origin`; tier-1 cases get one evaluator,
tier-2 cases get two (score.py:283-292).

**A Dataset is the exam paper.** Eight cases plus report-level evaluators,
named `map-writes-the-test` (score.py:294-298). Calling
`dataset.evaluate_sync(task_fn, ...)` runs every case through one task function
and produces a Report; the harness runs it once per arm with concurrency 1
(score.py:517-523).

**An Evaluator grades one case.** Three flavors appear here:

- `GraphFact` is a deterministic script check, a plain dataclass whose
  `evaluate` does substring checks on symbol and file name (score.py:143-170).
  No model anywhere.
- `RuleCitation` is a second agent: it sends the question, the answer, and the
  allowed chunk ids to a small structured-output agent that must return a
  `CitationVerdict` with a boolean and exactly one cited id (score.py:100-140),
  then checks that citation against the allowed list (score.py:173-197).
- `LLMJudge` is pydantic-evals' built-in model grader, given the rubric and
  pinned to local Ollama so the default cloud judge never fires
  (score.py:77-97).

**A ReportEvaluator grades the whole experiment.** `ScoreboardReport` walks the
report's cases and failures, sums passes by tier, tool calls, and tokens,
computes cost, and attaches the row as experiment metadata plus a rendered
table (score.py:200-263). It is the piece that turns per-case verdicts into
the scoreboard.

Two mechanics of `evaluate_sync` are worth knowing before reading a run. A
case that raises or times out does not vanish: it lands in the report's
failures list, and the scoreboard counts it into its tier's total as a
non-pass (score.py:223-226), so a crashed agent reads as a low score, not as a
missing row. And the harness runs with concurrency 1 (score.py:520), so the
eight cases of an arm execute in a fixed order and a live trace of the run
reads top to bottom like a story.

**Tests fake the whole thing.** This is the part to imitate. CI never needs
Ollama. A fake chat model called `Phraser` returns canned task JSON parsed
from the prompt's own fact lines (tests/test_tasks.py:68-101); a
`retrieve_factory` fakes the bridge (tests/test_tasks.py:115-128); an
`AlwaysPass` evaluator fakes the judge (tests/test_score.py:13-16); a
stand-in `task_fn` fakes the agent, answering from the prompt so the tests can
assert exactly which context each arm received (tests/test_score.py:24-33);
and the metrics tests fake the agent at the CLI boundary so the real sink code
runs against real Postgres (tests/test_score_metrics.py:49-92). The real
models are configuration; the exam's logic is tested without them.

## 8. The judge and the circularity objection

The objection writes itself: a model writes the tasks and a model grades them,
so the exam is graded by the same species that took it. The PRD names the risk
and the answer (docs/prd.md:248): the tiers are reported separately, and tier 1
is deterministic script checking on graph facts.

That answer has teeth because of how the tiers were built. Tier 1's grader is
a substring check with no model in it (score.py:143-170), and its answer key
is the graph edge, which no model produced at scoring time. If every model in
the room were replaced by a coin flip, tier 1 would still be a factual
examination of whether the agent knows the code. That is the trust floor: a
score of four out of four on tier 1 means the agent demonstrably learned the
repository's structure.

Tier 2 is where the model judge lives, so tier 2 is reported as its own
column, never merged into tier 1 (score.py:208-219). The sink stores
`passed_tier1` and `passed_tier2` as separate columns (metrics.py:17-18) and
the Results section shows them as separate bars (grafana/dashboards/
map-writes-the-test.json, panel title "Tasks passed by tier"). Two guards keep the
tier-2 judge honest in the narrow sense that matters: the judge must cite one
of the allowed chunk ids, and a second check independently verifies the
citation is on the case's allowed list (score.py:100-140, 173-197). The judge
cannot pass an answer by vibes; it must point at the rule.

Say it in one line: the model writes the wording, the join writes the facts,
and the facts are checkable without trusting the grader.

## 9. OSSIE: the contract that tells the truth

OSSIE is a semantic contract format: a YAML file that declares datasets (a
table, a primary key, typed fields) plus `ai_context` blocks, which are
instructions a future agent or tool should follow when using those datasets.
The map's contract declares eight datasets: `retrieved_chunks` (the
`policy_chunks` table), `sections`, `graph_nodes`, `graph_edges`, and the four
metrics tables `runs`, `task_results`, `tool_calls`, and `gate_metrics`
(ossie/map-writes-the-test.yaml:29-330). Its `ai_context` instructions read
like the handbook in miniature: read the map before exploring, ground domain
answers in retrieved chunk ids and cite them, swap the corpus by replacing the
folder and re-ingesting (ossie/map-writes-the-test.yaml:17-23).

The honest history matters. The first version of the YAML declared four
datasets, two of which were `graphify.graph.nodes` and `graphify.graph.edges`:
sources that were never Postgres tables at all, just a JSON file's shape
frozen into a contract. Nothing read the file, so nothing noticed. That is the
failure mode of paper contracts: a document that asserts and is never checked
drifts into fiction while looking professional.

The current version is built to be un-fakeable. The graph tables are
materialized into real Postgres tables by `ossie materialize`, which drops and
recreates `graph_nodes` and `graph_edges` inside one transaction from a
graphify file (ossie.py:21-37 and 54-102), so the fixture graph at
eval/demo-graph.json becomes queryable rows. And `python -m app ossie
validate` proves the whole contract live: it schema-checks the YAML, then
queries `information_schema` for every declared table and column
(ossie.py:142-182). It fails loud, exiting nonzero and naming the first thing
that is wrong, a missing table or a missing column, by exact name
(ossie.py:174, ossie.py:181; the CLI returns the code at cli.py:154-157).
Silent success is the only success. The contract and the database cannot
disagree quietly anymore.

## 10. Determinism and the cost story

Every knob that could wobble is pinned.

- Temperature 0 and seed 77 on the agent (agent.py:67), on the judge model
  (score.py:89), on the judge evaluator settings (score.py:96), and on the
  citation agent (score.py:120).
- Pinned models by configuration: the Ollama model defaults to `qwen3:8b` and
  the embedding model has an explicit default (config.py:13-14). Pin both in
  the environment; the PRD warns the local environment and the code default
  can drift (docs/prd.md:277).
- Deterministic ranking: reciprocal rank fusion with k equal to 60 over the
  two lanes, a fixed formula on ranks, not on scores (retrieve.py:59-76).
- Deterministic task selection: the same graph always yields the same four
  edges, because the sort key is the edge itself (graph.py:68-69, 85-87).

Rerun the command and you should get the same exam, the same grades, and the
same board. That is what makes the scoreboard evidence rather than anecdote.

The cost story rides along for free and is the sleeper metric. Every scored
row carries a dollar figure computed by a local formula: prompt tokens times
the configured input rate, completion tokens times the output rate, plus tool
calls times a per-call rate (score.py:68-74; rates default to $0.15 and $0.60
per million and $0.001 per tool call at config.py:17-19). Nothing here is a
cloud invoice; it is a consistent local price model, which is exactly what a
comparison needs. The judge's tokens are counted too (score.py:181-182), so
grading is not free in the books.

Why cost is the sleeper: a lost agent explores expensively. The PRD states it
as the design intent, "a lost agent explores expensively, a mapped agent goes
direct" (docs/prd.md:89). The bare arm's tool-call count is the wander; the
map arm's is the direct route; the dollar column converts that difference into
the language budgets are defended in. Watch the cost panel drop while the
score climbs and you have the whole business case in one screenshot.

Run the formula once on the seeded bare arm so it stops being abstract. The
test fake reports 100 prompt and 20 completion tokens per case, and the judge
fake reports 10 and 5 per tier-2 grade (tests/test_score_metrics.py:82-90;
tests/test_score.py:35-43). Eight cases and four grades give 840 prompt
tokens, 180 completion tokens, and 24 tool calls. The bill: 840 x $0.15 per
million, plus 180 x $0.60 per million, plus 24 x $0.001, which is $0.0242
(scratch/seed-reports carries the exact figure). The map arm's 16 tool calls
land at $0.0162. Same model, same questions: the difference is pure
exploration waste, priced.

## 11. The board

The observability layer is one sink and one board. The sink, `MetricsSink`,
creates and fills four tables (metrics.py:150-236):

- `runs`: one row per context arm per run, keyed on run id plus context, with
  tasks total and passed, passes per tier, tool calls, and cost (metrics.py:10-23).
- `task_results`: one row per scored case, with tier, pass flag, the origin
  JSON, and the tier-2 grounding distance (metrics.py:25-37).
- `tool_calls`: per-case tool-call counts, reconcilable with the runs row
  (metrics.py:39-48).
- `gate_metrics`: one appended row per mapping gate run with the wall time,
  node and edge counts, and the full gate document (metrics.py:50-60).

The board is provisioned as code at grafana/dashboards/map-writes-the-test.json.
It is one kiosk page in four sections and refreshes every 5 seconds. The time
picker is hidden: every panel reads the latest run id or the latest gate row,
so the dashboard clock does not select the data.

Arm names on the board are Baseline (no map), Map only, and Map + rules. Each
arm keeps one color on every comparison, and those colors are not red, amber,
or green. Red, amber, and green appear only on the pass rate and on the gate
result. The leading-arm tile says Best only when one arm strictly leads on
tasks passed. A tie stays a tie. The takeaway sentence is computed from that
same latest run.

- Results, family a. The takeaway, the leading-arm tile, the pass rate, and
  tasks passed by tier. Pass rate is green at 100 percent, amber in between,
  and red at none.
- Cost and efficiency, families b and c. Tool-call totals and cost totals for
  the latest run, drawn as bars with the value on the bar. Fewer calls is
  better. Lower cost is better. Cost keeps four decimal places. These are arm
  totals, not averages.
- Retrieval quality, family d. Tier 2 grounding distance, one row per task.
  Lower is better. The distance is fixed when the task list is built and
  copied onto every arm (score.py:464), so the table is not an arm score.
- Diagnostics. The per-task record (task, tier, arm, pass or fail, distance,
  tool calls) and earlier-run lines for families b and c. Cost is a run total
  and is not repeated on the task rows. Eight tasks is a small sample, and
  the section says so. Family e is the gate: mapping time with a sparkline of
  earlier gate runs, plus node count and edge count. Those counts describe
  the map. The passed flag is the only gate value with a direction.

What the seeded screenshot shows. The seed recipe (docs/demo-rig.md:53-76)
runs one full pass with real Postgres and fake models, the same fakes the
metrics tests use (tests/test_score_metrics.py:49-92). The resulting board
state is the demo's opening image: the bare arm passed 4 of 8 (tier 1 zero of
four, tier 2 four of four) while both the map and map_rules arms passed 8 of 8,
and the bare arm spent 24 tool calls against 16 for each mapped arm. The
numbers come straight from the seeded reports (scratch/seed-reports), where
the fake agent spends 3 calls per case bare and 2 with the map
(tests/test_score_metrics.py:56-64). Four versus eight with context; twenty-four
versus sixteen tool calls. On the board those arms read Baseline (no map),
Map only, and Map + rules. The board is at
http://127.0.0.1:3000/d/map-writes-the-test?kiosk (docs/demo-rig.md:43).

One caveat to keep honest: those are seeded, synthetic numbers rehearsing the
demo shape, not measured model results. Measured numbers come from the real
run path in the next section.

## 12. Glossary

- **map**: the repository knowledge artifact, two halves: module mermaid for
  humans, graphify graph.json for agents (docs/prd.md:106; tasks.py:342-353).
- **graph**: the machine-readable half of the map: nodes and typed,
  confidence-tagged edges (graph.py:12-21).
- **corpus**: the swappable folder of public industry documents, ingested into
  the `policy_chunks` and `sections` tables (docs/prd.md:106).
- **bridge**: the retrieve-only command `python -m app retrieve` that turns a
  text query into chunk JSON and logs every chunk id (bridge.py:22-46;
  cli.py:65-68).
- **tier 1**: a code-fact task. The question is a fixed template over one graph
  edge, graded by a deterministic script (tasks.py:84-89; score.py:145-170).
- **tier 2**: a domain-rule task, derived from an edge plus retrieved chunks,
  graded by a model judge plus a citation check (score.py:173-197).
- **golden exam**: This is old, and not part of eval harness. The Minion exam,
  `python -m app eval`, loads eval/goldens.json and runs each question through
  the same search the ask path uses. Search reads policy_chunks.
  (evaluate.py:73-119; cli.py:49-61).
- **case**: one pydantic-evals exam item: inputs, metadata holding the answer
  key and origin, and its evaluators (score.py:283-292).
- **arm**: one context level of the three-run protocol: bare, map, map_rules
  (score.py:29-33).
- **judge**: the tier-2 grader: an LLMJudge pinned to local Ollama plus the
  citation verifier agent (score.py:77-97, 100-140).
- **jailed agent**: the repo-reading agent whose two tools cannot resolve a
  path outside the repository root and which stops at 12 tool calls
  (agent.py:22-31; agent.py:10).
- **grounding distance**: the mean cosine distance of the chunks a tier-2 task
  cites; how far the corpus had to reach to ground the question
  (tasks.py:303-309).
- **OSSIE**: the semantic contract YAML declaring the eight datasets and the
  agent instructions, schema-checked and validated live against the database
  (ossie/map-writes-the-test.yaml; ossie.py:142-182).
- **sink**: `MetricsSink`, the Postgres writer behind the board, filling runs,
  task_results, tool_calls, and gate_metrics (metrics.py:150-236).
- **gate**: the mapping scale run, `python -m app gate`, that recorded the five
  Fineract numbers and appends a gate_metrics history row (cli.py:82-84;
  metrics.py:207-236; eval/fineract-gate.json).

### Pairs that get confused

- **score vs eval**: `python -m app score` is the harness (cli.py:69-77).
  This is old, and not part of eval harness: `python -m app eval` loads
  eval/goldens.json and runs each Minion question through the same search the
  ask path uses. Search reads policy_chunks. (cli.py:49-61).
- **map vs corpus**: the map is exact and inside the repo; the corpus is prose
  and outside it. The map aims the questions, the corpus fills them
  (tasks.py:252-257).
- **tier 1 vs tier 2**: graph fact vs domain rule; script grader vs judge
  (score.py:143-170; score.py:173-197). Tier 1 is the trust floor.
- **graph vs mermaid**: two representations of one map. The graph is the
  agent-facing artifact, mermaid the human-facing one, module-level only
  (docs/prd.md:106; graph.py:162-184).
- **run vs arm**: an arm is one context level; a run is all three arms under
  one shared run id (score.py:29-33; score.py:464).
- **sink vs board**: the sink writes Postgres (metrics.py:150-236); the board
  reads the latest run on a 5-second refresh and hides the time picker
  (docs/demo-rig.md:45-47).

## 13. Where to go deeper

Documents, in reading order:

- docs/prd.md: the specification of record. Section 5 has the system overview,
  section 6 the requirement statuses, Appendix B the gate protocol and result.
- docs/demo-rig.md: the operational recipe. Start checklist, seed recipe, the
  real-run replay path, teardown.
- docs/decision-log.md: rejected and deferred options with the reasoning and
  the named conditions that would reopen each one.
- docs/demo-pipeline.html: the end-to-end data flow as a diagram, where every
  blue box is a thing that exists.

Commands, the ones that matter:

```
python -m app score --repo P --graph P --output P   # the whole harness
python -m app gate --repo P --output P              # mapping scale run
python -m app ossie materialize --graph P           # graph into Postgres
python -m app ossie validate                        # contract, live
python -m app live                                  # watch a score run
python -m app compare DIR                           # three reports side by side
python -m app eval                                  # This is old, and not part of eval harness. Minion goldens.json.
python -m app retrieve "query"                      # the bridge, raw
```

A suggested reading path through the code, one sitting:

1. The join in miniature: tests/test_tasks.py:131-167, where a fake model
   fails to invent evidence and a bad citation kills the run.
2. The generator itself: tasks.py:209-339. Tier 1 is the template at
   tasks.py:84-89. The tier-2 phrasing contract the model must obey is
   tasks.py:160-167.
3. One run start to finish: score.py:417-547, the eight steps of section 6 in
   executable form.
4. The graders: score.py:143-197, script then judge then citation.
5. The board's inputs: metrics.py:150-236, then the seed recipe
   (docs/demo-rig.md:53-76) and watch the panels fill.

Sources live in the files this handbook cited throughout: tasks.py, score.py,
agent.py, graph.py, bridge.py, ossie.py, metrics.py, and their tests. When
code and handbook disagree, the code wins, and the handbook should be fixed
the same hour.

---

Document status: v1.0, 7 October 2026. Written against branch
feat/eval-harness at commit 30ec7fa; the PDF is built from docs/handbook.md
and lives outside the repository per the tracked-binary policy.
