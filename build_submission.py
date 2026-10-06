# Internal and Confidential - Not for External Distribution.
"""Assemble the Week 6 submission PDF from repo artifacts, live captures, and
optionally provided screenshots.

Usage (run from the repo root, any Python):

    python build_submission.py                 # build HTML + PDF, print status
    python build_submission.py --no-pdf        # HTML only
    python build_submission.py --open          # also open the PDF folder

How it fills each deliverable, in priority order:
  1. PROVIDED   a screenshot you dropped into submission_assets/ named
                NN-<anything>.png (01..11) is used verbatim for that item.
  2. LIVE       when Postgres (and for asks, Ollama) is reachable, the script
                runs the real command and embeds its real output.
  3. RENDERED   content derived from committed repo files (code listings,
                policy headers, eval records, diagnosis).
Anything missing is marked MISSING in red with the exact command to run,
never faked.

Output: submission/minirag-week6-submission.html and .pdf
"""

from __future__ import annotations

import html as html_mod
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "submission_assets"
OUTDIR = ROOT / "submission"
VENV_PY = ROOT / "myenv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]

STATUS_ORDER = {"PROVIDED": 0, "LIVE": 1, "RENDERED": 2, "STALE": 3, "MISSING": 4}


# --------------------------------------------------------------------------
# environment probes (fail soft, never start anything)


def probe_db() -> bool:
    out, _, code = run_venv(
        "from app.config import get_settings\n"
        "import psycopg\n"
        "url = get_settings().database_url.replace('postgres://', 'postgresql://', 1)\n"
        "conn = psycopg.connect(url, connect_timeout=2)\n"
        "conn.execute('SELECT 1')\n"
        "conn.close()\n"
        "print('ok')\n",
        timeout=30,
    )
    return code == 0 and "ok" in out


def probe_ollama() -> bool:
    out, _, code = run_venv(
        "import urllib.request\n"
        "from app.config import get_settings\n"
        "req = urllib.request.Request(get_settings().ollama_host.rstrip('/') + '/api/tags')\n"
        "urllib.request.urlopen(req, timeout=2).read()\n"
        "print('ok')\n",
        timeout=30,
    )
    return code == 0 and "ok" in out


def probe_gh() -> list[dict]:
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", "ci", "--limit", "8",
             "--json", "databaseId,displayTitle,conclusion,createdAt,url"],
            capture_output=True, text=True, timeout=20, shell=True,
        )
        return json.loads(out.stdout) if out.returncode == 0 else []
    except Exception:
        return []


def run_capture(args: list[str] | str, timeout: int = 900) -> tuple[str, str, int]:
    shell = isinstance(args, str)
    proc = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True,
        timeout=timeout, shell=shell,
    )
    return proc.stdout, proc.stderr, proc.returncode


def run_venv(code: str, timeout: int = 600) -> tuple[str, str, int]:
    return run_capture([PY, "-c", code], timeout=timeout)


# --------------------------------------------------------------------------
# provided assets


def provided_assets() -> dict[int, Path]:
    found: dict[int, Path] = {}
    if not ASSETS.exists():
        return found
    for path in sorted(ASSETS.iterdir()):
        match = re.match(r"(\d\d)", path.name)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"} and match:
            found.setdefault(int(match.group(1)), path)
    return found


# --------------------------------------------------------------------------
# live captures


MINIMAL_LOOP_SRC = '''
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
    conn.execute("CREATE TABLE minimal_loop (t TEXT, embedding vector(768))")
    for text, vector in zip(texts, vectors):
        conn.execute("INSERT INTO minimal_loop VALUES (%s, %s)", (text, vector))
    query = embed_texts(["what is the mileage reimbursement rate?"])[0]
    best = conn.execute(
        "SELECT t, embedding <=> %s::vector AS distance"
        " FROM minimal_loop ORDER BY distance LIMIT 1",
        (query,),
    ).fetchone()
    conn.execute("DROP TABLE minimal_loop")
print("closest:", best[0])
print("distance:", round(float(best[1]), 4))
'''


def live_minimal_loop() -> tuple[str, str, int]:
    return run_venv(MINIMAL_LOOP_SRC, timeout=300)


def live_ask(question: str, extra: str = "") -> tuple[str, str, int]:
    return run_capture(
        [PY, "-m", "app", "ask", question] + ([extra] if extra else []),
        timeout=600,
    )


def live_eval(args: list[str]) -> tuple[str, str, int]:
    return run_capture([PY, "-m", "app", "eval"] + args, timeout=900)


def live_trace(question: str) -> tuple[str, str, int]:
    code = f"""
import json
from app.pgadapter import PgAdapter
from app.reranker import CrossEncoderReranker
from app.trace import trace_ask
result = trace_ask({question!r}, database_adapter=PgAdapter(),
                   language_model=_Fake(), reranker=CrossEncoderReranker())
print(json.dumps(result, indent=2))
"""
    code = (
        "class _Fake:\n"
        "    def chat(self, prompt):\n"
        "        return '{\"answer\": \"TRACE MODE: canned generation; the retrieval stages below are real.\", \"section\": \"\"}'\n"
    ) + code
    return run_venv(code, timeout=600)


# --------------------------------------------------------------------------
# rendered (from committed files) content helpers


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def policy_headers() -> list[tuple[str, str]]:
    out = []
    for path in sorted((ROOT / "Policy").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        header = []
        for line in lines[1:]:
            if line.startswith("## "):
                break
            if line.strip():
                header.append(line.strip())
        out.append((path.name, "\n".join(header)))
    return out


def eval_record(rel: str) -> dict | None:
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except Exception:
        return None


def golden_count() -> int:
    try:
        return len(json.loads(read("eval/goldens.json")))
    except Exception:
        return 0


def ci_green_run(runs: list[dict]) -> dict | None:
    for run in runs:
        if run.get("conclusion") == "success":
            return run
    return None


# --------------------------------------------------------------------------
# html rendering


def esc(text: str) -> str:
    return html_mod.escape(text)


def terminal_block(title: str, body: str, status: str) -> str:
    return (
        f'<div class="terminal"><div class="term-head">'
        f'<span class="chip {status.lower()}">{status}</span>'
        f'<span class="term-title">{esc(title)}</span></div>'
        f"<pre>{esc(body.rstrip())}</pre></div>"
    )


def code_block(rel: str) -> str:
    lines = read(rel).splitlines()
    numbered = "\n".join(f"{i:>4}  {line}" for i, line in enumerate(lines, 1))
    return (
        f'<div class="codefile"><div class="term-head">'
        f'<span class="chip rendered">code</span>'
        f'<span class="term-title">{esc(rel)}</span></div>'
        f"<pre>{esc(numbered)}</pre></div>"
    )


def provided_image(n: int, path: Path, caption: str) -> str:
    relpath = path.relative_to(OUTDIR).as_posix()
    return (
        f'<div class="shot"><div class="term-head">'
        f'<span class="chip provided">provided</span>'
        f'<span class="term-title">{esc(caption)}</span></div>'
        f'<img src="{relpath}" alt="deliverable {n}"></div>'
    )


def missing_box(command: str, why: str) -> str:
    return (
        f'<div class="missing"><strong>MISSING evidence, run to capture:</strong>'
        f"<pre>{esc(command)}</pre><p>{esc(why)}</p></div>"
    )


def copy_button_src() -> str:
    return ""  # (unused, kept for structure)


def build_html(status: list[dict], sections: list[str]) -> str:
    now = datetime.now().strftime("%B %d, %Y %H:%M")
    toc_rows = "".join(
        f'<tr><td class="n">{item["n"]}</td><td>{esc(item["title"])}</td>'
        f'<td><span class="chip {item["status"].lower()}">{item["status"]}</span></td></tr>'
        for item in status
    )
    css = """
@page { size: A4; margin: 14mm 13mm 16mm 13mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9.5pt;
       line-height: 1.45; color: #1f2937; margin: 0; }
.cover { text-align: center; padding-top: 60px; break-after: page; }
.cover h1 { font-size: 24pt; color: #0f766e; margin: 8px 0; }
.cover .sub { font-size: 12pt; color: #374151; }
.cover .meta { margin-top: 26px; color: #6b7280; font-size: 9.5pt; }
h2 { font-size: 13.5pt; color: #0f766e; border-bottom: 1.5px solid #99f6e4;
     padding-bottom: 4px; margin: 20px 0 8px; break-after: avoid; }
.section { break-before: page; }
h3 { font-size: 11pt; color: #115e59; margin: 14px 0 5px; break-after: avoid; }
p { margin: 5px 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 8.4pt; }
th { background: #0f766e; color: #fff; text-align: left; padding: 4px 6px;
     border: 1px solid #0d5f58; }
td { padding: 3px 6px; border: 1px solid #d1d5db; vertical-align: top;
     word-wrap: break-word; }
tr:nth-child(even) td { background: #f0fdfa; }
tr { break-inside: avoid; }
.terminal, .codefile { margin: 8px 0; break-inside: auto; }
.terminal pre, .codefile pre, .missing pre {
  background: #0f172a; color: #e2e8f0; border-radius: 5px;
  padding: 9px 11px; font-family: Consolas, monospace; font-size: 7.9pt;
  line-height: 1.35; white-space: pre-wrap; word-wrap: break-word;
  overflow-wrap: break-word; margin: 0;
}
.codefile pre { background: #f8fafc; color: #111827;
  border: 1px solid #d1d5db; border-left: 4px solid #0f766e; }
.term-head { background: #e0f2f1; border: 1px solid #b2dfdb; border-bottom: none;
  border-radius: 5px 5px 0 0; padding: 4px 8px; display: flex; gap: 8px;
  align-items: center; }
.codefile .term-head { background: #ecfdf5; }
.term-title { font-family: Consolas, monospace; font-size: 8.5pt; color: #134e4a; }
.chip { font-size: 7.5pt; font-weight: 700; padding: 1px 7px; border-radius: 9px;
  color: #fff; text-transform: uppercase; letter-spacing: 0.4px; }
.chip.live { background: #16a34a; } .chip.provided { background: #7c3aed; }
.chip.rendered { background: #0f766e; } .chip.stale { background: #d97706; }
.chip.missing { background: #dc2626; }
.missing { border: 1.5px dashed #dc2626; border-radius: 6px; padding: 8px 10px;
  margin: 8px 0; background: #fef2f2; break-inside: avoid; }
.missing pre { background: #7f1d1d; }
.missing p { color: #7f1d1d; font-size: 8.6pt; margin: 5px 0 0; }
.shot img { max-width: 100%; border: 1px solid #d1d5db; border-radius: 4px; }
.note { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 5px;
  padding: 6px 9px; font-size: 8.6pt; margin: 8px 0; break-inside: avoid; }
.toc td { font-size: 9pt; } .toc .n { width: 30px; font-weight: 700; color: #0f766e; }
.cmd { font-family: Consolas, monospace; background: #f3f4f6;
  border: 1px solid #e5e7eb; border-radius: 3px; padding: 0 4px; font-size: 8.3pt; }
"""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Mini RAG, Week 6 Submission</title><style>{css}</style></head>
<body>
<div class="cover">
  <div class="sub">GenSpark Forward Deployed Engineer 018-260818-FDE</div>
  <h1>Week 6: Build a RAG System from Scratch</h1>
  <div class="sub">Assignment submission, Mini RAG</div>
  <div class="meta">Ken Mann &middot; generated {esc(now)} &middot;
    repo: github.com/kenmann01/minirag (feat/rag)<br>
    Eleven deliverables in assignment order, plus a pipeline-trace appendix.<br>
    Each item is labeled: <span class="chip provided">provided</span>
    <span class="chip live">live</span>
    <span class="chip rendered">rendered</span>
    <span class="chip stale">stale</span>
    <span class="chip missing">missing</span></div>
</div>
<h2 style="margin-top:0">Contents and evidence status</h2>
<table class="toc"><tr><th>#</th><th>Deliverable</th><th>status</th></tr>
{toc_rows}</table>
{''.join(sections)}
</body></html>"""


# --------------------------------------------------------------------------
# the eleven sections


def section_01(prov: dict[int, Path], status: list[dict]) -> str:
    n, title = 1, "Source documents with the planted data quality issue"
    body = [f'<div class="section"><h2>Deliverable 1: {esc(title)}</h2>']
    st = "RENDERED"
    if n in prov:
        st = "PROVIDED"
        body.append(provided_image(n, prov[n], title))
    else:
        body.append(
            "<p>All ten policy files with their ingest-parsed header blocks. "
            "The planted defect: <strong>minion_expense_policy_2021.md</strong> and "
            "<strong>minion_expense_policy_2024.md</strong> share Document ID "
            "<strong>LAIR-POL-009</strong> with conflicting numbers; the 2021 file "
            "carries <code>Superseded-By: minion_expense_policy_2024.md</code>, "
            "the lineage pointer the retriever filters on.</p>"
        )
        rows = ""
        conflict = {
            "minion_expense_policy_2021.md",
            "minion_expense_policy_2024.md",
        }
        for name, header in policy_headers():
            mark = ' style="background:#fef2f2;"' if name in conflict else ""
            rows += f"<tr><td{mark}>{esc(name)}</td><td{mark}><pre style='margin:0;font-size:6.8pt;line-height:1.2'>{esc(header)}</pre></td></tr>"
        body.append(f"<table><tr><th>file</th><th>header block (as parsed at ingest)</th></tr>{rows}</table>")
        body.append(
            "<h3>The seven conflicting rules (2021 vs 2024)</h3>"
            "<table><tr><th>rule</th><th>2021 (superseded)</th><th>2024 (current)</th></tr>"
            "<tr><td>receipt-free threshold</td><td>under $20</td><td>under $25</td></tr>"
            "<tr><td>submission window</td><td>90 days</td><td>60 days</td></tr>"
            "<tr><td>approval tier 1</td><td>under $250</td><td>under $500</td></tr>"
            "<tr><td>approval tier 2</td><td>$250-$1,000</td><td>$500-$2,000</td></tr>"
            "<tr><td>approval tier 3</td><td>over $1,000</td><td>over $2,000</td></tr>"
            "<tr><td>mileage</td><td>$0.56/mile</td><td>$0.67/mile</td></tr>"
            "<tr><td>per diem domestic / intl</td><td>$60 / $80</td><td>$75 / $95</td></tr>"
            "<tr><td>entertainment cap</td><td>$100/person</td><td>$150/person</td></tr></table>"
        )
    status.append({"n": n, "title": title, "status": st})
    return "".join(body) + "</div>"


def section_02(prov: dict[int, Path], status: list[dict], db_ok: bool) -> str:
    n, title = 2, "Minimal embed-store-retrieve loop (day-one proof)"
    body = [f'<div class="section"><h2>Deliverable 2: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + [provided_image(n, prov[n], title)][0] + "</div>"
    body.append(
        "<p>Embeds two known texts, stores both, and proves the mileage line wins "
        "the cosine comparison for the query. Same embedder and store as the pipeline.</p>"
    )
    if db_ok:
        out, err, code = live_minimal_loop()
        text = (out + ("\n" + err if code else "")).strip()
        ok = code == 0 and "Mileage" in out
        body.append(terminal_block("python minimal_loop.py", text, "LIVE" if ok else "MISSING"))
        if ok:
            body.append('<p class="note">Retrieved the mileage line, not the lockdown drill line: loop proven.</p>')
            status.append({"n": n, "title": title, "status": "LIVE"})
        else:
            body.append(missing_box("docker compose up -d && python build_submission.py", "the loop run failed; fix and re-run this builder."))
            status.append({"n": n, "title": title, "status": "MISSING"})
    else:
        body.append(missing_box("docker compose up -d && python build_submission.py",
                                "Postgres is not reachable, so the live loop could not run."))
        body.append("<h3>The exact script that will produce this evidence</h3>")
        body.append(f"<div class='codefile'><div class='term-head'>"
                    f"<span class='chip rendered'>code</span>"
                    f"<span class='term-title'>minimal_loop.py</span></div>"
                    f"<pre>{esc(MINIMAL_LOOP_SRC.strip())}</pre></div>")
        status.append({"n": n, "title": title, "status": "MISSING"})
    return "".join(body) + "</div>"


def section_03(prov: dict[int, Path], status: list[dict]) -> str:
    n, title = 3, "Chunking, embedding, and vector store code"
    body = [f'<div class="section"><h2>Deliverable 3: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    body.append(
        "<p>Small-to-big chunking: child windows (800-1200 chars, sentence-aligned, "
        "120+ overlap) are embedded and keyword-indexed; the full parent section "
        "rides along and is what the prompt sees.</p>"
    )
    for rel in ("app/chunking.py", "app/embeddings.py", "app/ingest.py"):
        body.append(code_block(rel))
    status.append({"n": n, "title": title, "status": "RENDERED"})
    return "".join(body) + "</div>"


def section_04(prov: dict[int, Path], status: list[dict], db_ok: bool, ollama_ok: bool) -> tuple[str, str | None]:
    n, title = 4, "Basic RAG pipeline end to end"
    body = [f'<div class="section"><h2>Deliverable 4: {esc(title)}</h2>']
    ask_json = None
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>", None
    q = "What approvals do I need for a $1,200 expense?"
    if db_ok and ollama_ok:
        out, err, code = live_ask(q)
        text = (out + ("\n" + err if code else "")).strip()
        body.append(terminal_block(f"python -m app ask \"{q}\"", text, "LIVE" if code == 0 else "MISSING"))
        try:
            ask_json = json.loads(out)
        except Exception:
            pass
        status.append({"n": n, "title": title, "status": "LIVE" if code == 0 else "MISSING"})
    else:
        need = "Postgres" if not db_ok else "Ollama"
        body.append(missing_box(f"{need} up, then: python -m app ask \"{q}\"",
                                f"{need} is not reachable; the real end-to-end answer could not be captured."))
        body.append("<h3>The ask path this command exercises (cli.py)</h3>")
        body.append(code_block("app/cli.py"))
        status.append({"n": n, "title": title, "status": "MISSING"})
    return "".join(body) + "</div>", ask_json


def section_05(prov: dict[int, Path], status: list[dict], db_ok: bool) -> str:
    n, title = 5, "Hybrid retrieval code and where it beats vector-only"
    body = [f'<div class="section"><h2>Deliverable 5: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    body.append(code_block("app/retrieve.py"))
    body.append(
        "<p>The A/B case is the golden <code>form-zx-4491</code> (\"What does form "
        "ZX-4491 authorize?\", dress code s11). Its chunk sits at vector rank 26, "
        "outside the top-20 vector lane, but the keyword lane matches it exactly, "
        "so hybrid retrieves it and vector-only misses it.</p>"
    )
    hybrid = vector = None
    if db_ok:
        out1, err1, c1 = live_eval(["--output", "submission/record-hybrid.json"])
        out2, err2, c2 = live_eval(["--retriever", "vector", "--output", "submission/record-vector.json"])
        try:
            hybrid = json.loads((ROOT / "submission/record-hybrid.json").read_text())
            vector = json.loads((ROOT / "submission/record-vector.json").read_text())
        except Exception:
            pass
        if hybrid and vector:
            rows = ""
            for h, v in zip(hybrid["results"], vector["results"]):
                flip = "PASS" if h["passed"] else "FAIL"
                vflip = "PASS" if v["passed"] else "MISS"
                mark = ' style="background:#dcfce7;font-weight:700"' if h["passed"] != v["passed"] else ""
                rows += (f"<tr><td>{esc(h['id'])}</td><td>{flip}</td><td>{vflip}</td>"
                         f"<td>{'hybrid wins' if h['passed'] and not v['passed'] else ('vector-only miss' if not v['passed'] else '')}</td></tr>")
            body.append(
                f"<h3>Live A/B, same build</h3>"
                f"<table><tr><th>golden</th><th>hybrid</th><th>vector-only</th><th></th></tr>{rows}</table>"
                f"<p>hybrid summary: {hybrid['summary']['passed']}/{hybrid['summary']['total']}; "
                f"vector-only summary: {vector['summary']['passed']}/{vector['summary']['total']}"
                f" (exit {c2}, nonzero because a golden MISSed).</p>"
            )
            st = "LIVE"
        else:
            body.append(terminal_block("python -m app eval (hybrid + vector A/B)", (err1 or err2).strip(), "MISSING"))
            st = "MISSING"
        status.append({"n": n, "title": title, "status": st})
    else:
        body.append(missing_box(
            "python -m app eval --output eval/record-hybrid.json\n"
            "python -m app eval --retriever vector --output eval/record-vector.json",
            "Postgres is not reachable: no live A/B. The committed "
            "eval/record-vector.json still totals 9 goldens, so it predates the "
            "form-zx-4491 case and must be regenerated to show the flip."))
        status.append({"n": n, "title": title, "status": "MISSING"})
    return "".join(body) + "</div>"


def section_06(prov: dict[int, Path], status: list[dict]) -> str:
    n, title = 6, "Reranking code"
    body = [f'<div class="section"><h2>Deliverable 6: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    body.append(
        "<p>Cross-encoder <code>cross-encoder/ms-marco-MiniLM-L-6-v2</code> scores "
        "each (question, parent text) pair, keeps the best child per section, "
        "and passes the top five, best first, to generation.</p>"
    )
    body.append(code_block("app/reranker.py"))
    status.append({"n": n, "title": title, "status": "RENDERED"})
    return "".join(body) + "</div>"


def section_07(prov: dict[int, Path], status: list[dict]) -> str:
    n, title = 7, "Evaluation test set and harness code"
    body = [f'<div class="section"><h2>Deliverable 7: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    body.append(
        "<p>Ten golden cases (recall@5 by section prefix, case-insensitive fact "
        "checks, exact-refusal pin) and the harness that runs them through the "
        "real pipeline, cache bypassed.</p>"
    )
    body.append(code_block("eval/goldens.json"))
    body.append(code_block("app/evaluate.py"))
    status.append({"n": n, "title": title, "status": "RENDERED"})
    return "".join(body) + "</div>"


def section_08(prov: dict[int, Path], status: list[dict], db_ok: bool) -> str:
    n, title = 8, "Evaluation harness terminal output with recall and accuracy"
    body = [f'<div class="section"><h2>Deliverable 8: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    if db_ok:
        out, err, code = live_eval(["--output", "eval/record.json"])
        text = (out + ("\n" + err if code else "")).strip()
        body.append(terminal_block("python -m app eval", text or "(no output)", "LIVE" if code == 0 else "MISSING"))
        status.append({"n": n, "title": title, "status": "LIVE" if code == 0 else "MISSING"})
    else:
        record = eval_record("eval/record.json")
        g = golden_count()
        total = record["summary"]["total"] if record else "?"
        stale = record and total != g
        body.append(
            f'<p class="note"><strong>STALE:</strong> Postgres is not reachable, so this is the '
            f'committed eval/record.json: {record["summary"]["passed"] if record else "?"}/'
            f'{total} passed, but goldens.json now holds {g} cases. Regenerate with '
            f'<span class="cmd">python -m app eval</span> once the database is up.</p>'
        )
        if record:
            body.append(terminal_block("committed eval/record.json (recomputed summary)", json.dumps(record["summary"], indent=2), "STALE"))
        try:
            goldens = json.loads(read("eval/goldens.json"))
            rows = "".join(
                f"<tr><td>{esc(g['id'])}</td><td>{esc(g['kind'])}</td>"
                f"<td>{esc(g['question'])}</td><td>{esc(', '.join(g['must_contain']) or '-')}</td></tr>"
                for g in goldens
            )
            body.append("<h3>What the harness measures: the ten-golden exam</h3>"
                        "<table><tr><th>id</th><th>kind</th><th>question</th><th>must contain</th></tr>"
                        f"{rows}</table>")
        except Exception:
            pass
        body.append(missing_box("docker compose up -d && python -m app eval",
                                "captures the real 10-case harness table."))
        status.append({"n": n, "title": title, "status": "STALE"})
    return "".join(body) + "</div>"


def section_09(prov: dict[int, Path], status: list[dict], db_ok: bool, ollama_ok: bool) -> str:
    n, title = 9, "Planted-issue question, flawed answer, and written diagnosis"
    body = [f'<div class="section"><h2>Deliverable 9: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    q = "How much can I spend on food each day?"
    if db_ok and ollama_ok:
        out, err, code = live_ask(q)
        body.append(terminal_block(f"python -m app ask \"{q}\"", (out + ("\n" + err if code else "")).strip(), "LIVE" if code == 0 else "MISSING"))
        out2, err2, code2 = live_ask(q, "--include-superseded")
        body.append(terminal_block(
            f"python -m app ask \"{q}\" --include-superseded",
            (out2 + ("\n" + err2 if code2 else "")).strip(),
            "LIVE" if code2 == 0 else "MISSING"))
        body.append('<p class="note">The filtered ask returns $75 citing 2024; the bypassed ask '
                    're-admits the stale 2021 duplicate so the $60 answer can be reproduced on demand. '
                    'Bypassed asks are never cached.</p>')
        status.append({"n": n, "title": title, "status": "LIVE" if code == 0 and code2 == 0 else "MISSING"})
    else:
        body.append(missing_box(
            f"python -m app ask \"{q}\"\npython -m app ask \"{q}\" --include-superseded",
            "Postgres/Ollama not reachable: no live pair. The written diagnosis below is committed."))
        status.append({"n": n, "title": title, "status": "MISSING"})
    body.append("<h3>Written diagnosis (docs/part6-diagnosis.md)</h3><pre style='background:#f8fafc;border:1px solid #d1d5db;border-radius:5px;padding:9px 11px;font-size:8.2pt;white-space:pre-wrap'>"
                + esc(read("docs/part6-diagnosis.md")) + "</pre>")
    return "".join(body) + "</div>"


def section_10(prov: dict[int, Path], status: list[dict], ask_json: dict | None) -> str:
    n, title = 10, "Source attribution output"
    body = [f'<div class="section"><h2>Deliverable 10: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    if ask_json and ask_json.get("citation"):
        body.append(terminal_block("citation from the Deliverable 4 ask",
                                   json.dumps(ask_json["citation"], indent=2), "LIVE"))
        body.append("<p>Every answer carries document, effective date, and section; the full "
                    "retrieved chunk list rides along in the same response.</p>")
        status.append({"n": n, "title": title, "status": "LIVE"})
    else:
        record = eval_record("eval/record.json")
        cite = None
        if record:
            for r in record["results"]:
                if r.get("citation"):
                    cite = r["citation"]
                    break
        if cite:
            body.append(terminal_block("citation from the committed eval/record.json",
                                       json.dumps(cite, indent=2), "RENDERED"))
            first = next((r for r in record["results"] if r.get("citation")), None)
            if first:
                body.append(terminal_block(
                    "retrieved chunk ids behind that citation (attribution evidence)",
                    "\n".join(first["ranked_chunk_ids"]), "RENDERED"))
            status.append({"n": n, "title": title, "status": "RENDERED"})
        else:
            body.append(missing_box("python -m app ask \"How much can I spend on food each day?\"",
                                    "no citation artifact available."))
            status.append({"n": n, "title": title, "status": "MISSING"})
    return "".join(body) + "</div>"


def section_11(prov: dict[int, Path], status: list[dict], runs: list[dict]) -> str:
    n, title = 11, "Passing pipeline run (CI)"
    body = [f'<div class="section"><h2>Deliverable 11: {esc(title)}</h2>']
    if n in prov:
        status.append({"n": n, "title": title, "status": "PROVIDED"})
        return "".join(body) + provided_image(n, prov[n], title) + "</div>"
    run = ci_green_run(runs)
    if run:
        rows = "".join(
            f"<tr><td>{esc(str(r['databaseId']))}</td><td>{esc(r.get('displayTitle') or '')}</td>"
            f"<td>{esc(str(r.get('conclusion')))}</td><td>{esc(str(r.get('createdAt', ''))[:19])}</td></tr>"
            for r in runs[:5]
        )
        body.append(
            f"<p>Latest green run: <strong>{esc(str(run['databaseId']))}</strong> "
            f"({esc(str(run.get('createdAt', ''))[:19])})<br>"
            f"<span class='cmd'>{esc(run['url'])}</span></p>"
            f"<h3>Recent runs (gh run list --workflow ci)</h3>"
            f"<table><tr><th>id</th><th>title</th><th>conclusion</th><th>created</th></tr>{rows}</table>"
            f"<p class='note'>Screenshot the green run page from this URL for the graded image, "
            f"or drop it into submission_assets/11-ci.png and rebuild.</p>"
        )
        status.append({"n": n, "title": title, "status": "LIVE"})
    else:
        body.append(missing_box("gh run list --workflow ci\ngh run view <run-id> --web",
                                "no green run found via gh; push a commit or run the workflow manually."))
        status.append({"n": n, "title": title, "status": "MISSING"})
    return "".join(body) + "</div>"


def appendix_trace(prov: dict[int, Path], db_ok: bool) -> str:
    body = ['<div class="section"><h2>Appendix: pipeline trace (ask-trace viewer)</h2>']
    body.append(
        "<p>Beyond the rubric: <code>python -m app serve</code> opens "
        "<strong>app/static/index.html</strong> on http://127.0.0.1:8765. Each ask "
        "returns every retrieval stage: stored chunks, cache hit/miss, the vector "
        "lane, the parsed keyword query and its lane, RRF fusion, cross-encoder "
        "ranking, what passed to generation, and the cache-store decision.</p>"
    )
    if db_ok:
        out, err, code = live_trace("What triggers the Villain Protocol?")
        if code == 0 and out.strip().startswith("{"):
            try:
                data = json.loads(out)
                stages = [
                    {
                        "stage": t["name"],
                        "detail": (
                            f'{len(t.get("chunks", []))} chunks'
                            if "chunks" in t
                            else t.get("status", t.get("stored", ""))
                        ),
                    }
                    for t in data.get("trace", [])
                ]
                rows = "".join(f"<tr><td>{esc(s['stage'])}</td><td>{esc(str(s['detail']))}</td></tr>" for s in stages)
                body.append(f"<p>Live trace for <em>What triggers the Villain Protocol?</em> "
                            f"(canned generation, real retrieval stages, cache bypassed "
                            f"neither: this went through the real path):</p>"
                            f"<table><tr><th>stage</th><th>detail</th></tr>{rows}</table>")
                body.append("<p class='note'>Full stage-by-stage chunk lists are in "
                            "submission/trace-villain-protocol.json; the HTML viewer renders "
                            "these interactively. Screenshot it for extra credit.</p>")
                (ROOT / "submission" / "trace-villain-protocol.json").write_text(out, encoding="utf-8")
            except Exception:
                body.append(terminal_block("trace_ask", (err or out).strip(), "MISSING"))
        else:
            body.append(terminal_block("trace_ask", (err or "failed").strip(), "MISSING"))
    else:
        body.append(missing_box("docker compose up -d && python -m app serve",
                                "database down: no live trace. Launch the viewer to screenshot it."))
    body.append("<h3>The trace recorder (app/trace.py) and its server (app/serve.py)</h3>")
    body.append(code_block("app/trace.py"))
    body.append(code_block("app/serve.py"))
    body.append("</div>")
    return "".join(body)


# --------------------------------------------------------------------------
# main


def main() -> int:
    no_pdf = "--no-pdf" in sys.argv
    OUTDIR.mkdir(exist_ok=True)
    prov = provided_assets()

    print("probing environment (nothing will be started)...")
    db_ok = probe_db()
    ollama_ok = probe_ollama() if db_ok else False
    runs = probe_gh() if not no_pdf or True else []
    print(f"  postgres: {'UP' if db_ok else 'down'}   ollama: {'UP' if ollama_ok else 'down'}"
          f"   gh runs: {len(runs)}   provided assets: {sorted(prov)}")

    status: list[dict] = []
    sections = [
        section_01(prov, status),
        section_02(prov, status, db_ok),
        section_03(prov, status),
    ]
    sec4_html, ask_json = section_04(prov, status, db_ok, ollama_ok)
    sections.append(sec4_html)
    sections.extend([
        section_05(prov, status, db_ok),
        section_06(prov, status),
        section_07(prov, status),
        section_08(prov, status, db_ok),
        section_09(prov, status, db_ok, ollama_ok),
        section_10(prov, status, ask_json),
        section_11(prov, status, runs),
        appendix_trace(prov, db_ok),
    ])

    html = build_html(status, sections)
    html_path = OUTDIR / "minirag-week6-submission.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"wrote {html_path}")

    if not no_pdf:
        chrome = next((p for p in CHROME_CANDIDATES if p.exists()), None)
        if chrome is None:
            print("chrome not found; HTML written but PDF skipped")
            return 1
        pdf_path = OUTDIR / "minirag-week6-submission.pdf"
        subprocess.run(
            [str(chrome), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={OUTDIR / '.chrome-profile'}",
             f"--print-to-pdf={pdf_path}",
             html_path.as_uri()],
            capture_output=True, timeout=180,
        )
        print(f"wrote {pdf_path}" if pdf_path.exists() else "PDF FAILED")

    print("\nevidence status:")
    for item in sorted(status, key=lambda i: (i["n"], STATUS_ORDER.get(i["status"], 9))):
        print(f"  [{item['status']:>8}] {item['n']:>2}. {item['title']}")
    worst = max((STATUS_ORDER.get(i["status"], 9) for i in status), default=9)
    print("\nnext step:" if worst >= 3 else "\nready:",
          "start Postgres (docker compose up -d) and Ollama, then re-run this builder"
          if worst >= 3 else "all captured evidence is real and current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
