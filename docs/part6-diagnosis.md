# Part 6: Diagnosing the Planted Data Quality Issue

The corpus ships with exactly one realistic defect: a stale 2021
duplicate of the expense policy filed under the same document ID as the
current 2024 version (both are LAIR-POL-009), with conflicting numbers
(the 2021 domestic meal per diem is $60/day against the 2024 $75/day,
and the mileage rate is $0.56 against $0.67). This note documents the
probe, the three-question diagnosis, the fix that was chosen, and how to
reproduce the flawed behavior on demand.

## The probe question

```
python -m app ask "How much can I spend on food each day?"
```

Before the lineage fix, this question could return $60/day citing the
2021 duplicate. Today it returns $75/day citing the 2024 section.

## The three-question method

**1. Was it retrieval?** No. Retrieval is measured directly: the golden
exam's per-diem case requires the 2024 travel-expenses section in the
final five and passes (`python -m app eval`, recall 10/10). Both lanes and
the reranker deliver the governing section; nothing in the retrieval
path prefers the stale document.

**2. Was it generation or grounding?** No. The generator answers only
from the excerpts it is given; the citation gate forces the exact
refusal whenever the model names a section that was not retrieved, so it
cannot invent a $60 answer out of thin air. Given only current sections,
the answer is correct every time.

**3. Was it the source data?** Yes. With the lineage filter bypassed
(reproducing the pre-fix state of the corpus), the superseded 2021
section re-enters both retrieval lanes and reaches the model alongside
the current one, and per-diem answers quoting $60/day become possible
again. The conflicting number exists only because a stale duplicate
lives in the source set under one document ID. **Root cause: the source
data, not the retrieval logic and not the generation logic.**

## The fix: lineage, not prompt patches

The fix stamps lineage at ingest and filters at retrieval. The 2021 file
carries a `**Superseded-By:** minion_expense_policy_2024.md` header;
chunking stores it in the `superseded_by` column; both retrieval lanes
filter `superseded_by IS NULL`, so normal answers never see superseded
rules.

Explicitly rejected: patching the prompt to say "prefer 2024 documents"
or "the per diem is $75". Prompt patches hardcode facts that go stale
the next time a policy changes, do not survive new documents, and mask
data problems instead of fixing them. The poisoned golden
(`per-diem-poisoned`) pins the fix permanently: it demands the current
number and fails the exam if the stale one ever leaks back into an
answer.

## Reproducing the defect on demand

The `--include-superseded` flag on ask and eval is the opt-in lineage
bypass. It is strictly opt-in, defaults preserve the filtered behavior,
and bypassed asks never touch the question cache.

Deterministic retrieval-level proof (the stale section reaches the
model's candidate set only under the flag):

```
pytest -q -k lineage
python -m app eval --include-superseded --output eval/record-bypass.json
```

The record's `ranked_chunk_ids` then contain
`minion_expense_policy_2021:s5` for the per-diem goldens; without the
flag no 2021 id appears anywhere in the record.

Flawed-answer capture with the real generator (owner's screenshot for
deliverable 9):

```
python -m app ingest
python -m app ask "How much can I spend on food each day?"
python -m app ask "How much can I spend on food each day?" --include-superseded
```

The first command shows the correct $75 answer with the 2024 citation.
The second re-exposes the pre-fix behavior: the stale section is in the
model's excerpts, and the capture shows either the flawed $60 answer
citing 2021 or the recovered behavior citing 2024, depending on which
excerpt the generator commits to at temperature 0. Either way the
diagnosis stands on the deterministic retrieval-level evidence above:
with bad source data admitted, the pipeline faithfully serves the bad
number; the defect is in the corpus, and the lineage filter is what
keeps it out.
