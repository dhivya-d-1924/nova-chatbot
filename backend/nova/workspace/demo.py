"""Simulated Google (Calendar, Gmail) and Microsoft Graph (Outlook Calendar, Teams, mailbox settings) providers.

Used by the automated tests and, when WORKSPACE_DEMO=true (development/testing only; refused in production), to try
the workspace without real accounts. They replace only the lowest HTTP function of each connector, so every layer above
(OAuth with PKCE, token refresh, throttling retries, normalisation, approvals, duplicate protection) runs for real.
Nothing is ever sent to Google, Microsoft or any person: "sent" items are kept in memory in this process."""
import base64
import email
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from email import policy


def _b64url_json(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def _b64(data):
    return base64.urlsafe_b64encode(data).decode()


class FakeGoogle:
    def __init__(self, me="me@corp.example"):
        self.me = me
        self.calls = []
        self.events = {}
        self.busy = {}          # email -> [(start_iso, end_iso)]
        self.messages = {}      # id -> message dict
        self.attachments = {}   # attachment id -> bytes
        self.sent = []
        self.drafts = []
        self.labels = [{"id": "Label_1", "name": "Existing"}]
        self.vacation = None
        self.errors = []        # queued GoogleError messages to raise on the next API calls (e.g. "429: rateLimitExceeded")
        self.n = 0

    def add_event(self, title, start, end, attendees=(), organizer=None):
        self.n += 1
        eid = f"gev{self.n}"
        self.events[eid] = {"id": eid, "summary": title, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                            "attendees": [{"email": a, "responseStatus": "accepted"} for a in attendees],
                            "organizer": {"email": organizer or self.me}, "status": "confirmed", "htmlLink": f"https://cal/{eid}"}
        return eid

    def add_message(self, frm, subject, body, thread=None, date=None, labels=("INBOX", "UNREAD"), attachments=(), cc=""):
        """attachments: [(filename, mime, bytes)]"""
        self.n += 1
        mid = f"msg{self.n:04d}"
        when = date or datetime.now(timezone.utc)
        headers = [{"name": "From", "value": frm}, {"name": "To", "value": self.me}, {"name": "Subject", "value": subject},
                   {"name": "Date", "value": when.strftime("%a, %d %b %Y %H:%M:%S +0000")},
                   {"name": "Message-ID", "value": f"<{mid}@mail.example>"}]
        if cc:
            headers.append({"name": "Cc", "value": cc})
        text = {"mimeType": "text/plain", "body": {"data": _b64(body.encode())}}
        if attachments:
            parts = [text]
            for i, (name, mime, data) in enumerate(attachments):
                aid = f"att{mid}x{i}" + "A" * 20
                self.attachments[aid] = data
                parts.append({"mimeType": mime, "filename": name, "body": {"attachmentId": aid, "size": len(data)}})
            payload = {"mimeType": "multipart/mixed", "headers": headers, "parts": parts, "body": {}}
        else:
            payload = {**text, "headers": headers}
        self.messages[mid] = {"id": mid, "threadId": thread or f"thr{self.n:04d}", "snippet": body[:100], "labelIds": list(labels),
                              "internalDate": str(int(when.timestamp() * 1000)), "payload": payload}
        return mid

    def _api_error(self):
        if self.errors:
            from ..integrations.google import GoogleError
            raise GoogleError(self.errors.pop(0))

    def __call__(self, method, url, data=None, headers=None, form=False, raw=False):
        self.calls.append((method, url, data))
        from ..integrations import google
        if url == google.TOKEN_URL:
            if data.get("grant_type") == "authorization_code":
                return {"access_token": "GAT", "refresh_token": "GRT", "expires_in": 3600, "scope": " ".join(google.SCOPES)}
            return {"access_token": "GAT2", "expires_in": 3600}
        if url == google.USERINFO_URL:
            return {"email": self.me, "email_verified": True, "hd": self.me.split("@")[1]}
        if url == google.REVOKE_URL:
            return {}
        self._api_error()
        path, _, query = url.partition("?")
        q = urllib.parse.parse_qs(query)
        path = path.replace(google.API, "")
        m = re.fullmatch(r"/calendar/v3/calendars/primary/events(?:/([^/]+))?", path)
        if m:
            eid = urllib.parse.unquote(m.group(1) or "")
            if method == "GET" and not eid:
                lo, hi = (datetime.fromisoformat(q[k][0].replace("Z", "+00:00")) for k in ("timeMin", "timeMax"))
                items = [e for e in self.events.values()
                         if datetime.fromisoformat(e["end"]["dateTime"]) > lo and datetime.fromisoformat(e["start"]["dateTime"]) < hi]
                return {"items": sorted(items, key=lambda e: e["start"]["dateTime"])}
            if method == "POST":
                self.n += 1
                ev = {**data, "id": f"gev{self.n}", "status": "confirmed", "htmlLink": f"https://cal/gev{self.n}", "organizer": {"email": self.me}}
                if data.get("conferenceData"):
                    ev["hangoutLink"] = f"https://meet.google.com/abc-{self.n}"
                self.events[ev["id"]] = ev
                return ev
            if eid not in self.events:
                raise google.GoogleError("404: not found")
            if method == "GET":
                return self.events[eid]
            if method == "PATCH":
                self.events[eid].update(data)
                return self.events[eid]
            if method == "DELETE":
                self.events.pop(eid)
                return {}
        if path == "/calendar/v3/freeBusy":
            return {"calendars": {i["id"]: ({"busy": [{"start": s, "end": e} for s, e in self.busy[i["id"]]]} if i["id"] in self.busy
                                            else {"errors": [{"reason": "notFound"}]}) for i in data["items"]}}
        if path == "/gmail/v1/users/me/profile":
            return {"emailAddress": self.me}
        if path == "/gmail/v1/users/me/messages" and method == "GET":
            skip = ("newer", "than", "older", "unread", "category", "promotions", "social", "updates", "inbox", "after", "before", "from")
            query_ = q.get("q", [""])[0].lower()
            words = [w for w in re.findall(r"[a-z]+", re.sub(r'from:"[^"]*"|from:\S+|after:\S+|before:\S+', " ", query_))
                     if len(w) > 3 and w not in skip]
            sender = re.search(r'from:"([^"]*)"|from:(\S+)', query_)
            hits = [m_ for m_ in sorted(self.messages.values(), key=lambda x: -int(x["internalDate"]))
                    if (not words or any(w in json.dumps(m_).lower() for w in words))
                    and (not sender or (sender.group(1) or sender.group(2)) in json.dumps(m_["payload"]["headers"]).lower())
                    and ("is:unread" not in query_ or "UNREAD" in m_["labelIds"])]
            start = int(q.get("pageToken", ["0"])[0])
            size = int(q.get("maxResults", ["10"])[0])
            out = {"messages": [{"id": x["id"]} for x in hits][start:start + size]}
            if start + size < len(hits):
                out["nextPageToken"] = str(start + size)
            return out
        m = re.fullmatch(r"/gmail/v1/users/me/messages/([^/]+)/attachments/([^/]+)", path)
        if m:
            return {"data": _b64(self.attachments[urllib.parse.unquote(m.group(2))]), "size": len(self.attachments[m.group(2)])}
        m = re.fullmatch(r"/gmail/v1/users/me/messages/([^/]+)(/modify)?", path)
        if m and m.group(1) != "send":
            mid = urllib.parse.unquote(m.group(1))
            if mid not in self.messages:
                raise google.GoogleError("404: not found")
            msg = self.messages[mid]
            if m.group(2):
                msg["labelIds"] = [x for x in msg["labelIds"] if x not in (data.get("removeLabelIds") or [])] + \
                    [x for x in data.get("addLabelIds") or [] if x not in msg["labelIds"]]
            return msg
        if path == "/gmail/v1/users/me/messages/send":
            parsed = email.message_from_bytes(base64.urlsafe_b64decode(data["raw"]), policy=policy.default)
            self.n += 1
            body = parsed.get_body(preferencelist=("plain",))
            sent = {"id": f"sent{self.n}", "threadId": data.get("threadId") or f"thr-s{self.n}", "to": parsed["To"], "subject": parsed["Subject"],
                    "in_reply_to": parsed["In-Reply-To"], "body": body.get_content() if body else "", "cc": parsed["Cc"],
                    "attachments": [(p.get_filename(), len(p.get_content())) for p in parsed.iter_attachments()]}
            self.sent.append(sent)
            return {"id": sent["id"], "threadId": sent["threadId"]}
        if path == "/gmail/v1/users/me/drafts":
            self.n += 1
            self.drafts.append(data)
            return {"id": f"draft{self.n}"}
        if path == "/gmail/v1/users/me/labels":
            if method == "POST":
                lab = {"id": f"Label_{len(self.labels) + 1}", "name": data["name"]}
                self.labels.append(lab)
                return lab
            return {"labels": self.labels}
        if path == "/gmail/v1/users/me/threads":
            threads = {}
            for x in self.messages.values():
                threads.setdefault(x["threadId"], []).append(x)
            return {"threads": [{"id": t} for t in threads]}
        m = re.fullmatch(r"/gmail/v1/users/me/threads/([^/]+)", path)
        if m:
            tid = urllib.parse.unquote(m.group(1))
            return {"id": tid, "messages": sorted([x for x in self.messages.values() if x["threadId"] == tid], key=lambda x: int(x["internalDate"]))}
        if path == "/gmail/v1/users/me/settings/vacation":
            self.vacation = data
            return data
        raise AssertionError(f"unexpected Google call {method} {url}")


TEAM_ID = "11111111-2222-3333-4444-555555555555"
CHANNEL_ID = "19:general@thread.tacv2"


class FakeGraph:
    def __init__(self, me="me@corp.example", tenant="tenant-1"):
        self.me, self.tenant = me, tenant
        self.calls = []
        self.events = {}
        self.schedule = {}      # email -> [(start_iso_utc_naive, end, status)]
        self.chats = [{"id": "19:chat1@thread.v2", "topic": "Project X", "chatType": "group", "webUrl": "https://teams/chat1"}]
        self.chat_msgs = {"19:chat1@thread.v2": [self.message("Rahul", "Deploy is delayed to Friday", "2026-10-22T05:00:00Z", "m1")]}
        self.teams = [{"id": TEAM_ID, "displayName": "Engineering",
                       "channels": [{"id": CHANNEL_ID, "displayName": "General"}]}]
        self.channel_msgs = {CHANNEL_ID: []}
        self.sent = []
        self.mailbox = None
        self.throttle = 0
        self.fail_refresh = False
        self.n = 0

    @staticmethod
    def message(sender, text, at=None, mid=None, sender_id=None):
        at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"id": mid or f"m{abs(hash((sender, text, at))) % 10**8}", "messageType": "message", "createdDateTime": at,
                "from": {"user": {"displayName": sender, "id": sender_id or f"{sender.lower().replace(' ', '.')}-id"}},
                "body": {"contentType": "html", "content": f"<p>{text}</p>"}}

    def add_chat(self, topic, messages, chat_id=None):
        """messages: [(sender, text, iso_time)]; a sender of 'Me' is the signed-in user."""
        self.n += 1
        cid = chat_id or f"19:chat{self.n}x@thread.v2"
        self.chats.append({"id": cid, "topic": topic, "chatType": "group", "webUrl": f"https://teams/{cid}"})
        self.chat_msgs[cid] = [self.message(s, t, at, sender_id="me-id" if s == "Me" else None) for s, t, at in messages]
        return cid

    def add_event(self, title, start, end, attendees=(), organizer=None):
        self.n += 1
        eid = f"AAMk{self.n:04d}="
        self.events[eid] = {"id": eid, "subject": title, "start": {"dateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000")},
                            "end": {"dateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000")},
                            "attendees": [{"emailAddress": {"address": a, "name": a}, "status": {"response": "accepted"}} for a in attendees],
                            "organizer": {"emailAddress": {"address": organizer or self.me}}, "showAs": "busy", "isCancelled": False,
                            "webLink": f"https://outlook/{eid}", "onlineMeeting": {"joinUrl": f"https://teams/meet/{eid}"}}
        return eid

    def _hits(self, query):
        words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2]
        out = []
        pools = [(cid, None, msgs) for cid, msgs in self.chat_msgs.items()] + [(None, ch, msgs) for ch, msgs in self.channel_msgs.items()]
        for cid, ch, msgs in pools:
            for m in msgs:
                text = re.sub(r"<[^>]+>", "", m["body"]["content"])
                if words and not any(w in text.lower() for w in words):
                    continue
                name = m["from"]["user"]["displayName"]
                res = {"id": m["id"], "createdDateTime": m["createdDateTime"], "webLink": f"https://teams/msg/{m['id']}",
                       "from": {"emailAddress": {"name": name, "address": f"{name.lower().replace(' ', '.')}@corp.example"}}}
                if cid:
                    res["chatId"] = cid
                else:
                    res["channelIdentity"] = {"teamId": TEAM_ID, "channelId": ch}
                out.append({"summary": text[:200], "resource": res})
        return out

    def __call__(self, method, url, data=None, headers=None, form=False):
        from . import microsoft
        self.calls.append((method, url, data))
        if url.startswith(microsoft.LOGIN):
            if data.get("grant_type") == "refresh_token" and self.fail_refresh:
                raise microsoft.GraphError(400, "invalid_grant: AADSTS70000 token revoked")
            return 200, {}, {"access_token": "MAT", "refresh_token": "MRT", "expires_in": 3600, "scope": " ".join(microsoft.scopes()),
                             "id_token": f"x.{_b64url_json({'tid': self.tenant, 'preferred_username': self.me})}.y"}
        if self.throttle:
            self.throttle -= 1
            err = microsoft.GraphError(429, "Too many requests")
            err.retry_after = "0"
            raise err
        path, _, query = url.replace(microsoft.GRAPH, "").partition("?")
        q = urllib.parse.parse_qs(query)
        if path == "/me":
            return 200, {}, {"id": "me-id", "mail": self.me, "displayName": "Me"}
        if path == "/me/calendarView":
            lo = q["startDateTime"][0].replace("Z", "")
            hi = q["endDateTime"][0].replace("Z", "")
            items = [e for e in self.events.values() if e["end"]["dateTime"][:19] > lo[:19] and e["start"]["dateTime"][:19] < hi[:19]]
            return 200, {}, {"value": items}
        m = re.fullmatch(r"/me/events(?:/([^/]+))?(/cancel|/decline)?", path)
        if m:
            eid = urllib.parse.unquote(m.group(1) or "")
            if method == "POST" and not eid:
                self.n += 1
                eid = f"AAMk{self.n:04d}="
                ev = {**data, "id": eid, "organizer": {"emailAddress": {"address": self.me}}, "webLink": f"https://outlook/{eid}",
                      "isCancelled": False, "showAs": "busy"}
                ev["start"] = {"dateTime": data["start"]["dateTime"] + ".0000000"}
                ev["end"] = {"dateTime": data["end"]["dateTime"] + ".0000000"}
                if data.get("isOnlineMeeting"):
                    ev["onlineMeeting"] = {"joinUrl": f"https://teams/meet/{eid}"}
                self.events[eid] = ev
                return 201, {}, ev
            if eid not in self.events:
                raise microsoft.GraphError(404, "not found")
            if m.group(2):
                self.events[eid]["isCancelled"] = True
                return 202, {}, {}
            if method == "PATCH":
                self.events[eid].update(data)
            return 200, {}, self.events[eid]
        if path == "/me/calendar/getSchedule":
            return 200, {}, {"value": [{"scheduleId": e, "scheduleItems": [
                {"status": st, "start": {"dateTime": s}, "end": {"dateTime": en}} for s, en, st in self.schedule[e]]}
                if e in self.schedule else {"scheduleId": e, "error": {"message": "not found"}} for e in data["schedules"]]}
        if path == "/me/chats":
            out = []
            for c in self.chats:
                msgs = self.chat_msgs.get(c["id"]) or []
                last = msgs[-1] if msgs else None
                prev = {"createdDateTime": datetime.now(timezone.utc).isoformat() if c["id"] == "19:chat1@thread.v2" else last["createdDateTime"],
                        "body": last["body"], "from": last["from"]} if last else {}
                out.append({**c, "members": [{"displayName": "Rahul"}], "lastMessagePreview": prev})
            out.sort(key=lambda c: (c.get("lastMessagePreview") or {}).get("createdDateTime", ""), reverse=True)
            return 200, {}, {"value": out}
        m = re.fullmatch(r"/(?:me/)?chats/([^/]+)/messages", path)
        if m:
            cid = urllib.parse.unquote(m.group(1))
            if method == "POST":
                self.n += 1
                self.sent.append({"chat": cid, "text": data["body"]["content"]})
                return 201, {}, {"id": f"sentmsg{self.n}", "webUrl": f"https://teams/{cid}/{self.n}"}
            return 200, {}, {"value": list(reversed(self.chat_msgs.get(cid, [])))}
        if path == "/chats" and method == "POST":
            return 201, {}, {"id": "19:oneonone@unq.gbl.spaces"}
        if path == "/me/joinedTeams":
            return 200, {}, {"value": [{"id": t["id"], "displayName": t["displayName"]} for t in self.teams]}
        m = re.fullmatch(r"/teams/([^/]+)/channels", path)
        if m:
            team = next((t for t in self.teams if t["id"] == urllib.parse.unquote(m.group(1))), None)
            return 200, {}, {"value": team["channels"] if team else []}
        m = re.fullmatch(r"/teams/([^/]+)/channels/([^/]+)/messages", path)
        if m:
            ch = urllib.parse.unquote(m.group(2))
            if method == "POST":
                self.n += 1
                self.sent.append({"channel": ch, "text": data["body"]["content"]})
                return 201, {}, {"id": f"chanmsg{self.n}", "webUrl": f"https://teams/channel/{self.n}"}
            return 200, {}, {"value": list(reversed(self.channel_msgs.get(ch, [])))}
        if path == "/search/query":
            return 200, {}, {"value": [{"hitsContainers": [{"hits": self._hits(data["requests"][0]["query"]["queryString"])}]}]}
        if path == "/me/mailboxSettings":
            self.mailbox = data
            return 200, {}, {}
        if path == "/me/mailboxSettings/automaticRepliesSetting":
            return 200, {}, (self.mailbox or {}).get("automaticRepliesSetting", {"status": "disabled"})
        raise AssertionError(f"unexpected Graph call {method} {url}")


# ------------------------------------------------------------------ demo data
def seeded(me, tenant="tenant-1"):
    """A small, believable workday: meetings on both calendars (one clash), a leave note, an urgent email with an
    attachment, a newsletter, a question waiting in Teams and a channel discussion."""
    fg, fm = FakeGoogle(me), FakeGraph(me, tenant)
    domain = me.split("@")[1]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    fg.add_event("Design review", now + timedelta(hours=1), now + timedelta(hours=2), [f"priya@{domain}"])
    fm.add_event("Client call", now + timedelta(hours=1, minutes=30), now + timedelta(hours=2, minutes=30), ["client@other.example"])
    fg.add_message(f"Priya <priya@{domain}>", "Leave", "I will be on leave " + (now + timedelta(days=1)).date().isoformat())
    fg.add_message(f"Arun Kumar <arun@{domain}>", "URGENT: production checkout errors",
                   "Checkout is failing for about 5% of users since 10:00. Can you confirm the rollback plan today? Logs attached.",
                   date=now - timedelta(hours=2), labels=("INBOX", "UNREAD", "IMPORTANT"),
                   attachments=[("checkout-errors.log", "text/plain", b"10:02 ERROR payment timeout\n10:05 ERROR payment timeout\n")])
    fg.add_message(f"Meena <meena@{domain}>", "Q4 roadmap review",
                   "Could you send me your team's Q4 priorities by Friday? Ravi will own the API section.",
                   date=now - timedelta(days=4), labels=("INBOX",))
    fg.add_message("Tech Weekly <no-reply@newsletter.example>", "This week in tech", "Ten stories you missed this week.",
                   date=now - timedelta(hours=5), labels=("INBOX", "UNREAD"))
    fm.chat_msgs["19:chat1@thread.v2"] = [
        FakeGraph.message("Me", "Is the deploy still on for Thursday?", iso(now - timedelta(hours=3)), "m0", "me-id"),
        FakeGraph.message("Rahul", "Deploy is delayed to Friday. Can you update the client and confirm the new window?",
                          iso(now - timedelta(hours=1)), "m1")]
    fm.channel_msgs[CHANNEL_ID] = [
        FakeGraph.message("Divya", "Deployment issue: the payment service times out under load. Ravi is adding a retry.",
                          iso(now - timedelta(hours=2)), "c1"),
        FakeGraph.message("Ravi", "Retry fix is in review; ETA tomorrow 12:00.", iso(now - timedelta(hours=1)), "c2")]
    return fg, fm


def install(me, tenant="tenant-1"):
    """Replace both connectors' HTTP layer with seeded simulated accounts (shared by everyone in this process)."""
    from ..integrations import google
    from . import microsoft
    fg, fm = seeded(me, tenant if tenant not in ("", "organizations", "common", "consumers") else "tenant-1")
    google._http = fg
    microsoft._http = fm
    return fg, fm
