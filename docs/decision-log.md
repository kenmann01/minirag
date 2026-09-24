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

## 8. Golden-level vector-only failure for the hybrid demo (deviation, 2026-09-24)

The spec asked for a hybrid-demo golden that fails under vector-only and
passes under hybrid. Probing the real stack showed this is not
constructible at this corpus scale: the keyword lane matches only chunks
containing every question stem, and any chunk containing the question's
rare exact phrase ("Villain Protocol" plus a trigger stem) also ranks in
the vector lane's top 20 of roughly 90 chunks (measured rank 2 under
maximal dilution; the next chunk after the phrase cluster sits far
behind at distance 0.65 against 0.56). Every keyword-reachable chunk is
vector-reachable, so no natural golden can fail one lane and pass the
other.

Kept: the A/B flag and honest A/B reporting, the Villain Protocol golden
as the exam's hybrid showcase, and the keyword-rescue mechanism proof at
the test seam (an exact-term chunk with an adversarial embedding that
hybrid retrieves and vector-only drops, the same pattern the suite
already used). This entry records the deviation from the frozen
blueprint.

Flip condition: per-lane candidate limits below the phrase-cluster size,
or a corpus large enough that the vector top 20 no longer covers every
exact-phrase chunk.
