# Submission Checklist

Eleven screenshots, in the deliverable order, each mapped to the exact
command or artifact that produces it. Prerequisites for real-generator
screenshots: Postgres up (`docker compose up -d`), Ollama up with the
model (`ollama serve`, `ollama pull qwen3:8b`), dependencies installed,
and the store bootstrapped (`python -m app ingest`).

## 1. Source documents, with the planted issue identified

```
ls Policy
sed -n '1,8p' Policy/minion_expense_policy_2021.md
sed -n '1,8p' Policy/minion_expense_policy_2024.md
```

Screenshot the three outputs together. The planted issue is visible in
the headers: both files carry Document ID LAIR-POL-009, and the 2021
file carries the `Superseded-By: minion_expense_policy_2024.md` lineage
pointer.

## 2. Minimal embed-store-retrieve loop (day-one proof)

There is no committed day-one script (the repo grew from the loop, not
around it), so replay it with this one-shot script. It embeds two known
texts, stores them, and proves the query retrieves the more relevant of
the two, using the same embedder and store as the pipeline:

```
python - <<'PY'
from app.embeddings import embed_texts
from app.pgadapter import PgAdapter

texts = [
    "Bunk assignments for the quarterly lockdown drill are taped to each bunk.",
    "Mileage in the Minion Van is reimbursed at $0.67 per mile.",
]
vectors = embed_texts(texts)
adapter = PgAdapter()
with adapter.connect() as conn:
    conn.execute("DROP TABLE IF EXISTS minimal_loop")
    conn.execute(
        "CREATE TABLE minimal_loop (t TEXT, embedding vector(768))"
    )
    for text, vector in zip(texts, vectors):
        conn.execute(
            "INSERT INTO minimal_loop VALUES (%s, %s)", (text, vector)
        )
    query = embed_texts(["what is the mileage reimbursement rate?"])[0]
    best = conn.execute(
        """
        SELECT t, embedding <=> %s::vector AS distance
        FROM minimal_loop ORDER BY distance LIMIT 1
        """,
        (query,),
    ).fetchone()
    conn.execute("DROP TABLE minimal_loop")
print("closest:", best[0])
print("distance:", round(float(best[1]), 4))
PY
```

Screenshot the output; it must retrieve the mileage line, not the
lockdown drill line.

## 3. Chunking, embedding, and vector store code

Open and screenshot these files:

```
app/chunking.py    # heading split, 800-1200 char child windows, 120+ overlap
app/embeddings.py  # Alibaba-NLP/gte-modernbert-base, normalized
app/ingest.py      # table schema, upsert, orphan cleanup, parent/child columns
```

## 4. Basic pipeline end to end on a sample question

```
python -m app ask "What approvals do I need for a $1,200 expense?"
```

Screenshot the JSON: grounded answer, citation, five retrieved chunks.

## 5. Hybrid retrieval code, and a query where it beats vector-only

Code:

```
app/retrieve.py    # both lanes, RRF fusion (k=60), the mode switch
```

Demonstration (exact-phrase rescue: hybrid keeps the chunk,
vector-only drops it):

```
pytest -q tests/test_evaluate.py -k vector_mode
```

Optional side-by-side table capture:

```
python -m app eval --output eval/record-hybrid.json
python -m app eval --retriever vector --output eval/record-vector.json
```

See docs/decision-log.md entry 8 for why the A/B tables alone do not
show a recall flip at this corpus scale.

## 6. Reranking code

```
app/reranker.py    # ms-marco cross-encoder, best-first, one child per section, top 5
```

## 7. Evaluation test set and harness code

```
eval/goldens.json  # the nine-case exam with expected sections and facts
app/evaluate.py    # recall@5 prefix rule, answer checks, run_exam, table
```

## 8. Terminal output of the harness with results

```
python -m app eval
```

Screenshot the per-golden table (target: 9/9 passed) and keep
`eval/record.json` as the machine-readable artifact.

## 9. Planted-issue question, flawed answer, written diagnosis

```
python -m app ask "How much can I spend on food each day?"
python -m app ask "How much can I spend on food each day?" --include-superseded
pytest -q -k lineage
```

Screenshot the two asks plus the passing lineage tests, alongside
docs/part6-diagnosis.md (the written diagnosis landing on source data
as the root cause).

## 10. Source attribution output

```
python -m app ask "How much can I spend on food each day?"
```

Screenshot the JSON's `citation` object (document, effective date,
section) and the `retrieved_chunks` list.

## 11. Passing pipeline run

The fast CI tier runs on every push and is the submission's green run:

```
gh run list --workflow ci --limit 3
gh run view <run-id>
```

Or open the Actions tab of the repository and screenshot the green
`fast` job. The manual `full` tier (real generator end to end) is
triggered with `gh workflow run ci` and screenshots the same way.
