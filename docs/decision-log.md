# Decision Log

Rejected and deferred options, with the reasoning and the named
conditions under which each decision would be revisited. Entries are
frozen in this order; the date is when the decision was made.

## 1. Decision-model layer (rejected 2026-09-20)

A typed decision layer (the TypeSafe "System One" style model, prototyped
as "Jev") was proposed to score and route asks with calibrated
probabilities before paying for generation.

Rejected because the stack runs a local Ollama generator: generation is
free, so there is no cost case. Quality judgment is done deterministically
by the golden exam (required facts, forbidden facts, exact refusal), so
there is no judge case. The layer cannot replace RAG (it has no index and
cannot generate), so it would sit as a shell around a pipeline that
already gates itself with the citation gate and the refusal path.

Flip condition: a multi-provider routing choice with real cost
differentials between generation hosts.

## 2. Semantic cache (rejected with the cache design)

The question cache is exact-match, keyed on the normalized question plus
the embedder, prompt, and model version. A semantic cache (embedding
similarity over questions) was rejected.

Near-duplicate questions legitimately earn different answers ("the $500
home stipend" and "the $500 approval tier"), a similarity threshold
creates stale-answer risk exactly where lineage matters, and the
evaluation harness, which bypasses the cache, would have needed a second
bypass for the semantic tier.

Flip condition: an ask-latency SLA that makes generation the bottleneck
while the corpus is frozen.

## 3. Alternate vector store (rejected at pipeline build)

The assignment suggested chromadb as the local vector store. Postgres
with pgvector was kept instead.

One service holds the relational lineage (the superseded_by column and
its filter) and the vectors; upserts of chunks, embeddings, and the
generated tsvector column commit in one transaction; there is no second
runtime to sync or boot. Chroma would have split chunk state across two
systems and pushed lineage filtering into application code.

Flip condition: the corpus outgrowing Postgres operational comfort (tens
of millions of chunks) or a multi-tenant isolation requirement.

## 4. L2 distance (rejected at pipeline build)

Embeddings are stored normalized and ranked with cosine distance
(`<=>`). L2 was rejected.

On normalized vectors the rankings agree, but cosine states the geometry
we actually validate, and L2 on unnormalized vectors would silently
overweight vector norm. The embedder runs with
`normalize_embeddings=True`, and the distance column is asserted as a
float in every retrieval test.

Flip condition: a future embedder that emits unnormalized vectors that
we choose not to normalize before storing.

## 5. Agentic retrieval loop (deferred to v2)

Multi-hop agentic retrieval (the model issuing tool calls to re-search)
was deferred.

Single-shot hybrid retrieval plus cross-encoder reranking answers the
whole exam; an agentic loop adds latency and nondeterminism to a CLI
contract whose value is a fast, citable answer, and the Week 6 material
treats agentic retrieval as an escalation path rather than a default.

Flip condition: multi-hop questions in the goldens that single-shot
retrieval demonstrably fails.

## 6. BM25 for the keyword lane (rejected 2026-09-24)

BM25 was considered for the keyword lane instead of Postgres full-text
search. ts_rank was kept.

The tsv column is generated and stored with the chunk, a GIN index
serves the match, and both lanes read the same table in one connection.
BM25 would add an external engine and a sync path to score a lane whose
job here is exact-phrase rescue, not ranking excellence; RRF only needs
ranks.

Flip conditions, any one of which reopens it: recall@5 evals showing
ts_rank ranking quality visibly behind BM25 (length normalization or
field weighting needed); a corpus in languages the `english`
text-search config mishandles; the keyword lane becoming the measured
p95 latency bottleneck inside Postgres.

## 7. Version-diff queries (deferred to a mechanical v2 2026-09-24)

"Show me what changed between the 2021 and 2024 expense policies" is
real and cheap to want. Deferred because the lineage pointer
(superseded_by) already exists and the trust case this arc pins is "the
current version wins", which the poisoned golden enforces. A diff view
is presentation over data the schema already holds.

Flip condition: documented user demand for change visibility, then ship
it as a query over the lineage pointer and both parent_texts.

## 8. Form ZX-4491 is the hybrid flip (measured 2026-09-25)

The Villain Protocol question is not the flip. Its section is vector rank
4 and keyword-reachable, so both lanes pass it. The flip is form ZX-4491,
one sentence in the dress-code laundry-cage section
(`minion_dress_code_policy:s11`). The question is "What does form ZX-4491
authorize?" Every stem the keyword lane requires is in that section. The
child window that holds the code is mostly cage procedure, so the
bi-encoder ranks that child 26. The vector lane keeps `LIMIT 20`, and
vector-only search does not return the section. Hybrid fusion still
includes the keyword hit, and the cross-encoder keeps the section in the
final five.

The vector limit stays 20. Goldens the keyword lane cannot save
(per-diem, the approval tiers, the home stipend) have vector ranks 1-3,
so the safe floor is 3. ZX-4491 at rank 26 is already outside that floor.
Dropping the limit is unnecessary.

The negated-embedding test remains a mechanism check of the keyword lane.
It is not the rubric demonstration. The demonstration is
`python -m app eval` against `python -m app eval --retriever vector`:
form-zx-4491 passes hybrid and misses vector-only.

Flip condition: an embedder that ranks the ZX-4491 child inside the
vector top 20. Lengthen the laundry-cage section until the rank falls
back outside 20. Do not drop the vector limit below the keyword-miss
floor.
