"""Safe building blocks for the agent's tools (no access to the app's own database here)."""
import ast
import http.client
import importlib.util
import ipaddress
import json
import math
import operator
import os
import re
import smtplib
import socket
import ssl
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser

from sqlalchemy import create_engine, event, inspect, text


class ToolError(Exception):
    """A problem the model (and user) should be told about in plain words."""


def _domains(var):
    return [d.strip().lower() for d in os.getenv(var, "").split(",") if d.strip()]


# ------------------------------------------------------------------ calculator
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "sin": math.sin, "cos": math.cos,
          "tan": math.tan, "abs": abs, "round": round, "min": min, "max": max, "floor": math.floor, "ceil": math.ceil}
_CONSTS = {"pi": math.pi, "e": math.e}


def calculate(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 200:
        raise ToolError("Give a short math expression, for example (12.5 * 8) / 3.")

    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow):
                if abs(b) > 1000:
                    raise ToolError("Exponent is too large.")
                a = float(a) if isinstance(a, int) and abs(a) > 10**6 else a
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ToolError("Unsupported expression. Use numbers, + - * / // % **, parentheses, pi, e and functions like sqrt().")

    try:
        result = ev(ast.parse(expression.strip(), mode="eval").body)
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("Division by zero.")
    except (SyntaxError, ValueError, OverflowError, TypeError):
        raise ToolError("Could not evaluate that expression.")
    return f"{expression.strip()} = {round(result, 10) if isinstance(result, float) else result}"


# ------------------------------------------------------------------ time
def current_time(tz=None):
    from zoneinfo import ZoneInfo
    name = (tz or "UTC").strip() or "UTC"
    try:
        now = datetime.now(ZoneInfo(name))
    except Exception:  # noqa: BLE001
        raise ToolError(f"Unknown timezone '{name}'. Use names like 'Asia/Kolkata' or 'UTC'.")
    return f"{now.strftime('%A, %d %B %Y, %H:%M:%S')} ({name}, UTC{now.strftime('%z')})"


# ------------------------------------------------------------------ web fetch (SSRF-safe)
def check_url(url):
    p = urllib.parse.urlparse(url if isinstance(url, str) else "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Only http(s) web addresses are allowed.")
    host = p.hostname.lower()
    allowed = _domains("ALLOWED_DOMAINS")
    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
        raise ToolError(f"{host} is not on the approved sources list.")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ToolError("Could not resolve that web address.")
    if any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
        raise ToolError("That address points to a private or internal network and is not allowed.")
    return url


def _guarded_create_connection(address, *args, **kwargs):
    """Connect, then verify the address actually connected to is public.
    This closes the DNS-rebinding gap between check_url() resolving a name and the HTTP client resolving it again."""
    sock = socket.create_connection(address, *args, **kwargs)
    try:
        peer = sock.getpeername()[0]
        if not ipaddress.ip_address(peer).is_global:
            raise ToolError("That address points to a private or internal network and is not allowed.")
    except ToolError:
        sock.close()
        raise
    return sock


class _SafeHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _guarded_create_connection


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _guarded_create_connection


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req, context=ssl.create_default_context())


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)  # every hop is re-checked
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    """No environment proxies by default: a proxy would hide the real destination from the IP check.
    Set FETCH_PROXY only if outbound traffic must go through a proxy that enforces its own egress rules."""
    proxy = os.getenv("FETCH_PROXY", "").strip()
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}), _SafeRedirect)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeHTTPHandler, _SafeHTTPSHandler, _SafeRedirect)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}

    def __init__(self):
        super().__init__()
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def fetch_url(url, limit=6000):
    check_url(url)
    # check_url() allows only public http(s) addresses
    req = urllib.request.Request(url, headers={"User-Agent": "NovaAgent/1.0 (+read-only)", "Accept": "text/html,text/plain"})  # noqa: S310
    try:
        with _opener().open(req, timeout=10) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and not (ctype.startswith("text/") or ctype in ("application/json", "application/xml", "application/xhtml+xml")):
                raise ToolError(f"Can't read content of type {ctype}.")
            raw = r.read(1_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Could not open that page ({type(e).__name__}).")
    body = raw.decode(charset, errors="replace")
    if "html" in ctype or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        ex = _TextExtractor()
        ex.feed(body)
        body = re.sub(r"[ \t]+", " ", "".join(ex.parts))
        body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
        title = ex.title.strip()
    else:
        title = ""
    return f"{title}\n{url}\n\n{body[:limit]}".strip()


# ------------------------------------------------------------------ web search
def search_available():
    prov = os.getenv("SEARCH_PROVIDER", "ddg").lower()
    if prov == "tavily":
        return bool(os.getenv("TAVILY_API_KEY"))
    if prov == "brave":
        return bool(os.getenv("BRAVE_API_KEY"))
    return bool(importlib.util.find_spec("ddgs") or importlib.util.find_spec("duckduckgo_search"))


def _http_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,  # noqa: S310  fixed https APIs
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
        return json.loads(r.read(2_000_000))


def web_search(query, n=5):
    if not isinstance(query, str) or not query.strip():
        raise ToolError("Give a search query.")
    prov = os.getenv("SEARCH_PROVIDER", "ddg").lower()
    want = n * 3 if _domains("ALLOWED_DOMAINS") else n
    try:
        if prov == "tavily":
            data = _http_json("https://api.tavily.com/search", {"api_key": os.getenv("TAVILY_API_KEY"), "query": query, "max_results": want})
            rows = [(r["title"], r["url"], r.get("content", "")) for r in data.get("results", [])]
        elif prov == "brave":
            data = _http_json("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": want}),
                              headers={"X-Subscription-Token": os.getenv("BRAVE_API_KEY"), "Accept": "application/json"})
            rows = [(r["title"], r["url"], r.get("description", "")) for r in data.get("web", {}).get("results", [])]
        else:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            with DDGS() as d:
                rows = [(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in d.text(query, max_results=want)]
    except ImportError:
        raise ToolError("Web search isn't installed. Run: pip install ddgs")
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The search service failed ({type(e).__name__}). Try again later.")
    allowed = _domains("ALLOWED_DOMAINS")
    if allowed:
        rows = [r for r in rows if any((urllib.parse.urlparse(r[1]).hostname or "").endswith(d) for d in allowed)]
    rows = rows[:n]
    if not rows:
        return "No results found" + (" on the approved sources." if allowed else ".")
    return "\n\n".join(f"{i}. {t}\n   {u}\n   {s[:300]}" for i, (t, u, s) in enumerate(rows, 1))


# ------------------------------------------------------------------ read-only business database
MAX_ROWS = 50
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|grant|revoke|exec|execute|call|"
    r"merge|vacuum|reindex|load_extension|outfile|dumpfile|handler|set)\b", re.I)
_engine = None


def db_configured():
    return bool(os.getenv("AGENT_DATABASE_URL", "").strip())


def _agent_engine():
    global _engine
    if _engine is None:
        url = os.getenv("AGENT_DATABASE_URL", "").strip()
        if not url:
            raise ToolError("No business database is configured.")
        _engine = create_engine(url, pool_pre_ping=True)

        @event.listens_for(_engine, "connect")
        def _readonly(dbapi_conn, _):  # database-level read-only where supported
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA query_only = ON" if url.startswith("sqlite") else "SET SESSION TRANSACTION READ ONLY")
            finally:
                cur.close()
    return _engine


def _allowed_tables():
    names = inspect(_agent_engine()).get_table_names()
    allow = {t.lower() for t in _domains("AGENT_DB_TABLES")}
    return [n for n in names if not allow or n.lower() in allow]


def describe_database():
    insp = inspect(_agent_engine())
    tables = _allowed_tables()
    if not tables:
        return "The database has no accessible tables."
    lines = []
    for t in tables:
        cols = ", ".join("%s %s" % (c["name"], c["type"]) for c in insp.get_columns(t))
        lines.append("%s(%s)" % (t, cols))
    return "\n".join(lines)


def run_query(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("Give a SELECT query.")
    stripped = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql.strip().rstrip(";"), flags=re.S)
    if ";" in stripped:
        raise ToolError("Only a single statement is allowed.")
    if not re.match(r"\s*(select|with)\b", stripped, re.I):
        raise ToolError("Only SELECT queries are allowed.")
    if _FORBIDDEN.search(stripped):
        raise ToolError("That query uses a keyword that is not allowed. This database is read-only.")
    allowed = {t.lower() for t in _allowed_tables()}
    ctes = {c.lower() for c in re.findall(r"(?:\bwith|,)\s*([A-Za-z_]\w*)\s+as\s*\(", stripped, re.I)}
    for ref in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", stripped, re.I):
        name = ref.split(".")[-1].lower()
        if name not in allowed and name not in ctes:
            raise ToolError(f"Table '{ref}' is not available. Use describe_database to see what you can query.")
    try:
        with _agent_engine().connect() as c:
            res = c.execute(text(stripped))
            rows = res.fetchmany(MAX_ROWS + 1)
            cols = list(res.keys())
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The query failed: {str(getattr(e, 'orig', e))[:200]}")
    more = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    if not rows:
        return "The query returned no rows."
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) if v is not None else "" for v in r) + " |" for r in rows]
    out = "\n".join(lines)[:6000]
    return out + (f"\n\n(Showing the first {MAX_ROWS} rows; there are more.)" if more else "")


# ------------------------------------------------------------------ email
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def send_email(to, subject, body):
    to = (to or "").strip() if isinstance(to, str) else ""
    if not EMAIL_RE.match(to):
        raise ToolError("Give exactly one valid recipient email address.")
    if not subject or len(subject) > 200 or not body or len(body) > 5000:
        raise ToolError("Subject (up to 200 characters) and body (up to 5000) are required.")
    allowed = _domains("EMAIL_ALLOWED_DOMAINS")
    if allowed and to.split("@")[1].lower() not in allowed:
        raise ToolError("Emails to that domain are not permitted.")
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        print(f"\n[agent email - NOT sent, no SMTP configured]\nTo: {to}\nSubject: {subject}\n\n{body}\n")
        return "Development mode: no mail server is configured, so the email was NOT delivered. It was printed in the server console."
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject.replace("\n", " "), to
    msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER", "")
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"The email could not be sent ({type(e).__name__}).")
    return f"Email sent to {to}."
