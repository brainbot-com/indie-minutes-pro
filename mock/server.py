"""Mock GitHub and Drive for building and testing the follow-up loop without
real accounts (standard library only, state in /data/mock/state.json).

GitHub (REST subset, same JSON shapes the follow-up agent reads):
  GET  /github/repos/<owner>/<repo>/issues?state=all&per_page=&page=
  POST /github/repos/<owner>/<repo>/issues              {title, body, labels}
  GET  /github/repos/<owner>/<repo>/issues/<n>/comments
  POST /github/repos/<owner>/<repo>/issues/<n>/comments {body}
Drive:
  POST /drive/append  {folder, doc, markdown} -> {url}
  GET  /drive/docs
Pages people can open (through Caddy at /mock/): /mock/, /mock/github/<owner>/<repo>/issues/<n>,
/mock/drive/<folder>/<doc>."""
import html
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

STATE = Path(os.environ.get("MOCK_STATE", "/data/mock/state.json"))
PUBLIC = os.environ.get("MOCK_PUBLIC_URL", "http://127.0.0.1:18099/mock").rstrip("/")
SEED = Path(__file__).with_name("seed_issues.json")
lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    state = {"repos": {}, "docs": {}}
    if SEED.exists():
        seed = json.loads(SEED.read_text())
        for repo, issues in seed.items():
            state["repos"][repo] = {"issues": [], "comments": {}}
            for i in issues:
                add_issue(state, repo, i["title"], i.get("body", ""), i.get("state", "open"))
    return state


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    tmp.replace(STATE)


def add_issue(state, repo, title, body, st="open", labels=()):
    r = state["repos"].setdefault(repo, {"issues": [], "comments": {}})
    n = len(r["issues"]) + 1
    issue = {"number": n, "title": title, "body": body, "state": st, "labels": [{"name": x} for x in labels],
             "html_url": f"{PUBLIC}/github/{repo}/issues/{n}", "created_at": now(), "comments": 0}
    r["issues"].append(issue)
    return issue


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False).encode()
                                                      if ctype == "application/json" else body.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype != "application/json" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        path, q = unquote(u.path), parse_qs(u.query)
        with lock:
            state = load()
        m = re.fullmatch(r"/github/repos/([^/]+/[^/]+)/issues", path)
        if m:
            issues = state["repos"].get(m.group(1), {}).get("issues", [])
            st = q.get("state", ["open"])[0]
            issues = [i for i in issues if st == "all" or i["state"] == st]
            per, page = int(q.get("per_page", ["30"])[0]), int(q.get("page", ["1"])[0])
            return self.send(200, issues[(page - 1) * per: page * per])
        m = re.fullmatch(r"/github/repos/([^/]+/[^/]+)/issues/(\d+)/comments", path)
        if m:
            return self.send(200, state["repos"].get(m.group(1), {}).get("comments", {}).get(m.group(2), []))
        if path == "/drive/docs":
            return self.send(200, [{"folder": k.split("/", 1)[0], "doc": k.split("/", 1)[1],
                                    "url": f"{PUBLIC}/drive/{quote(k)}", "entries": len(v)} for k, v in state["docs"].items()])
        if path in ("/mock", "/mock/"):
            return self.send(200, page_index(state), "text/html")
        m = re.fullmatch(r"/mock/github/([^/]+/[^/]+)/issues/(\d+)", path)
        if m:
            return self.send(200, page_issue(state, m.group(1), int(m.group(2))), "text/html")
        m = re.fullmatch(r"/mock/drive/(.+)", path)
        if m:
            return self.send(200, page_doc(state, m.group(1)), "text/html")
        if path == "/health":
            return self.send(200, {"ok": True})
        self.send(404, {"message": "Not Found"})

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        body = self.body()
        with lock:
            state = load()
            m = re.fullmatch(r"/github/repos/([^/]+/[^/]+)/issues", path)
            if m:
                issue = add_issue(state, m.group(1), body.get("title", ""), body.get("body", ""),
                                  labels=body.get("labels", []))
                save(state)
                return self.send(201, issue)
            m = re.fullmatch(r"/github/repos/([^/]+/[^/]+)/issues/(\d+)/comments", path)
            if m:
                repo, n = m.group(1), m.group(2)
                r = state["repos"].get(repo)
                if not r or int(n) > len(r["issues"]):
                    return self.send(404, {"message": "Not Found"})
                comments = r["comments"].setdefault(n, [])
                c = {"id": len(comments) + 1, "body": body.get("body", ""), "created_at": now(),
                     "html_url": f"{PUBLIC}/github/{repo}/issues/{n}#comment-{len(comments) + 1}"}
                comments.append(c)
                r["issues"][int(n) - 1]["comments"] = len(comments)
                save(state)
                return self.send(201, c)
            if path == "/drive/append":
                key = f"{body.get('folder', 'test')}/{body.get('doc', 'Meeting log')}"
                state["docs"].setdefault(key, []).append({"at": now(), "markdown": body.get("markdown", "")})
                save(state)
                return self.send(200, {"url": f"{PUBLIC}/drive/{quote(key)}", "entries": len(state["docs"][key])})
        self.send(404, {"message": "Not Found"})


STYLE = ("<style>body{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px}"
         "pre{white-space:pre-wrap;background:#f0efe9;padding:10px;border-radius:8px}"
         ".tag{font-size:12px;border:1px solid #ccc;border-radius:99px;padding:1px 8px}</style>")


def page_index(state):
    out = [STYLE, "<h1>Mock GitHub and Drive</h1><p>Stand-ins for testing; nothing here leaves the box.</p>"]
    for repo, r in state["repos"].items():
        out.append(f"<h2>{html.escape(repo)}</h2><ul>")
        for i in r["issues"]:
            out.append(f'<li><a href="github/{repo}/issues/{i["number"]}">#{i["number"]} {html.escape(i["title"])}</a>'
                       f' <span class="tag">{i["state"]}</span> {i["comments"]} comments</li>')
        out.append("</ul>")
    out.append("<h2>Drive docs</h2><ul>")
    for key, entries in state["docs"].items():
        out.append(f'<li><a href="drive/{quote(key)}">{html.escape(key)}</a>, {len(entries)} entries</li>')
    return "".join(out) + "</ul>"


def page_issue(state, repo, n):
    r = state["repos"].get(repo, {"issues": []})
    if n > len(r["issues"]):
        return "no such issue"
    i = r["issues"][n - 1]
    out = [STYLE, f'<p><a href="../../../../">mock index</a></p><h1>#{n} {html.escape(i["title"])}</h1>',
           f'<p class="tag">{i["state"]}</p><pre>{html.escape(i["body"])}</pre>']
    for c in r["comments"].get(str(n), []):
        out.append(f'<h3 id="comment-{c["id"]}">Comment {c["id"]} · {c["created_at"]}</h3><pre>{html.escape(c["body"])}</pre>')
    return "".join(out)


def page_doc(state, key):
    entries = state["docs"].get(key, [])
    out = [STYLE, f'<p><a href="../../">mock index</a></p><h1>{html.escape(key)}</h1>']
    for e in entries:
        out.append(f"<p class=tag>{e['at']}</p><pre>{html.escape(e['markdown'])}</pre>")
    return "".join(out)


def main():
    port = int(os.environ.get("PORT", "8099"))
    print(f"mock GitHub and Drive on :{port}, state {STATE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
