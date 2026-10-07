# Internal and Confidential - Not for External Distribution.
"""Serve a local page that compares the three scored runs."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PAGE = Path(__file__).resolve().parent / "static" / "compare.html"
_ORDER = ("bare", "map", "map_rules")


def load_summaries(report_dir: Path) -> list[dict]:
    """Read the three report files in run order."""
    summaries = []
    for name in _ORDER:
        path = report_dir / f"{name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        row = payload["scoreboard"]
        summaries.append(row)
    return summaries


def render_page(summaries: list[dict]) -> str:
    """Fill the offline comparison page with the three scoreboard rows."""
    template = _PAGE.read_text(encoding="utf-8")
    payload = json.dumps(summaries).replace("<", "\\u003c")
    return template.replace("__DATA__", payload)


def serve_compare(report_dir: Path, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Serve the comparison page until the process is stopped."""
    page = render_page(load_summaries(report_dir)).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"comparison at http://{host}:{port}", flush=True)
    server.serve_forever()
