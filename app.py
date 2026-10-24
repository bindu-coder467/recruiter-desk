"""C2C Recruiter Desk: serves the web UI and the Adzuna job search.

Run:  python app.py
Needs only Python 3.8+. Get a free key at https://developer.adzuna.com
"""
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = os.getenv("ADZUNA_APP_ID") or input("Adzuna app id: ").strip()
APP_KEY = os.getenv("ADZUNA_APP_KEY") or input("Adzuna app key: ").strip()

EXCLUDE = ["on my bench", "candidate available", "available consultants", "marketing my consultant"]
TAGS = ["C2C", "W2", "1099", "Full-Time", "Bench", "Hotlist", "Remote"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PAGES = int(os.getenv("ADZUNA_PAGES", "1"))  # free test keys have very few calls

# Official "Sign in with LinkedIn" (OpenID Connect). Optional.
LI_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LI_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LI_REDIRECT = os.getenv("LINKEDIN_REDIRECT_URI", "http://127.0.0.1:8000/auth/linkedin/callback")
LI_USER = {}  # in-memory: {"state": ..., "user": {...}}


def fetch(what, page):
    q = urllib.parse.urlencode({
        "app_id": APP_ID, "app_key": APP_KEY, "what": what,
        "max_days_old": 1,            # last 24 hours
        "results_per_page": 50, "sort_by": "date",
        "content-type": "application/json",
    })
    url = f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?{q}"  # US only
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r).get("results", [])


def search(what, keywords, extra=()):
    seen, jobs = set(), []
    for page in range(1, PAGES + 1):
        for j in fetch(what, page):
            text = f"{j.get('title', '')} {j.get('description', '')}"
            low = text.lower()
            if keywords and not any(k.lower() in low for k in keywords):
                continue
            if any(x in low for x in EXCLUDE) or any(
                    re.search(r"(?<!\w)" + re.escape(x.lower()) + r"(?!\w)", low) for x in extra):
                continue
            tags = [t for t in TAGS if re.search(r"\b" + re.escape(t) + r"\b", text, re.I)]
            for email in EMAIL_RE.findall(text) or [""]:
                key = email.lower() or j.get("redirect_url")
                if key in seen:
                    continue
                seen.add(key)
                jobs.append({
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "email": email.lower(),
                    "title": j.get("title", ""),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "url": j.get("redirect_url", ""),
                    "tags": tags,
                })
    return {"total": len(jobs), "jobs": jobs}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/auth/") or u.path == "/api/me":
            return self.auth(u)
        if u.path == "/api/search":
            p = urllib.parse.parse_qs(u.query)
            what = p.get("what", ["Java Developer"])[0]
            kws = [k for k in p.get("kw", [""])[0].split(",") if k]
            ex = [x.strip() for x in p.get("ex", [""])[0].split(",") if x.strip()]
            try:
                self.send(200, json.dumps(search(what, kws, ex)).encode(), "application/json")
            except Exception as e:
                self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        elif u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        else:
            self.send(404, b"Not found", "text/plain")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/extract":
            return self.send(404, b"Not found", "text/plain")
        try:
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("Set ANTHROPIC_API_KEY, then restart app.py.")
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            import extract_posts
            rows, stats = extract_posts.run(
                data.get("posts", [])[:60], key, extract_posts.MODEL,
                [x.strip() for x in data.get("exclude", "").split(",") if x.strip()])
            self.send(200, json.dumps({"rows": rows, "stats": stats}).encode(), "application/json")
        except Exception as e:
            self.send(500, json.dumps({"error": str(e)}).encode(), "application/json")

    def redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.end_headers()

    def auth(self, u):
        p = urllib.parse.parse_qs(u.query)
        ok = bool(LI_ID and LI_SECRET)
        if u.path == "/api/me":
            body = json.dumps({"configured": ok, "user": LI_USER.get("user")}).encode()
            return self.send(200, body, "application/json")
        if u.path == "/auth/logout":
            LI_USER.clear()
            return self.redirect("/")
        if not ok:
            return self.redirect("/?li=unconfigured")
        if u.path == "/auth/linkedin":
            LI_USER["state"] = secrets.token_urlsafe(16)
            q = urllib.parse.urlencode({
                "response_type": "code", "client_id": LI_ID, "redirect_uri": LI_REDIRECT,
                "state": LI_USER["state"], "scope": "openid profile email"})
            return self.redirect("https://www.linkedin.com/oauth/v2/authorization?" + q)
        if u.path == "/auth/linkedin/callback":
            if "code" not in p or p.get("state", [""])[0] != LI_USER.get("state"):
                return self.redirect("/?li=error")
            try:
                data = urllib.parse.urlencode({
                    "grant_type": "authorization_code", "code": p["code"][0],
                    "redirect_uri": LI_REDIRECT, "client_id": LI_ID,
                    "client_secret": LI_SECRET}).encode()
                with urllib.request.urlopen(urllib.request.Request(
                        "https://www.linkedin.com/oauth/v2/accessToken", data=data), timeout=20) as r:
                    token = json.load(r)["access_token"]
                req = urllib.request.Request("https://api.linkedin.com/v2/userinfo",
                                             headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(req, timeout=20) as r:
                    info = json.load(r)
                LI_USER["user"] = {"name": info.get("name", ""), "email": info.get("email", ""),
                                   "picture": info.get("picture", "")}
            except Exception:
                return self.redirect("/?li=error")
            return self.redirect("/?li=ok")
        self.send(404, b"Not found", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Running at http://127.0.0.1:8000  (Ctrl+C to stop)")
    threading.Timer(1, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    srv.serve_forever()
