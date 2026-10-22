"""Microsoft Teams through Microsoft Graph, as the signed-in user: recent chats, reading and searching messages,
sending chat and channel messages, listing teams and channels. Reading channel messages needs the
ChannelMessage.Read.All permission, which an administrator must approve."""
import html
import re
import urllib.parse
from datetime import timedelta

from .. import tools
from . import common, microsoft

CHAT_ID = re.compile(r"[A-Za-z0-9:@._\-]{5,300}")
GUID = re.compile(r"[0-9a-fA-F\-]{36}")


def _q(v):
    return urllib.parse.quote(v, safe="")


def _chat_id(v):
    v = str(v or "").strip()
    if not CHAT_ID.fullmatch(v):
        raise tools.ToolError("Use a chat id from teams_recent_chats or teams_search.")
    return v


def text_of(body):
    content = (body or {}).get("content") or ""
    if (body or {}).get("contentType") == "html":
        content = re.sub(r"<br\s*/?>|</p>|</div>", "\n", content, flags=re.I)
        content = html.unescape(re.sub(r"<[^>]+>", "", content))
    return content.strip()


def _msg(m):
    sender = ((m.get("from") or {}).get("user") or {}).get("displayName") or "(app)"
    return {"id": m.get("id"), "from": sender, "at": m.get("createdDateTime", ""), "text": text_of(m.get("body"))[:2000],
            "link": m.get("webUrl")}


def my_id(uid):
    return microsoft.graph(uid, "GET", "/me", params={"$select": "id"}).get("id", "")


def recent_chats(uid, limit=15):
    res = microsoft.graph(uid, "GET", "/me/chats", params={
        "$expand": "members,lastMessagePreview", "$top": str(min(limit, 50)),
        "$orderby": "lastMessagePreview/createdDateTime desc"})
    out = []
    for c in res.get("value", []):
        prev = c.get("lastMessagePreview") or {}
        members = [m.get("displayName") or m.get("email") for m in c.get("members") or []]
        sender = ((prev.get("from") or {}).get("user") or {})
        out.append({"id": c.get("id"), "topic": c.get("topic") or ", ".join(x for x in members if x)[:120],
                    "type": c.get("chatType"), "last": text_of(prev.get("body"))[:200],
                    "last_at": prev.get("createdDateTime"), "last_from": sender.get("displayName") or "",
                    "last_from_id": sender.get("id") or "", "link": c.get("webUrl")})
    return out


def chat_messages(uid, chat_id, limit=20):
    res = microsoft.graph(uid, "GET", f"/me/chats/{_q(_chat_id(chat_id))}/messages", params={"$top": str(min(limit, 50))})
    return [_msg(m) for m in res.get("value", []) if m.get("messageType") == "message"]


def needs_reply(uid, days=3, limit=15):
    """Chats whose latest message is from someone else in the last `days` days (they may be waiting for you).
    Questions and @mentions are flagged."""
    me = my_id(uid)
    since = (common.now_utc() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    out = []
    for c in recent_chats(uid, 30):
        if not c["last_at"] or c["last_at"][:19] < since or not c["last_from_id"] or c["last_from_id"] == me:
            continue
        c["question"] = "?" in c["last"]
        out.append(c)
        if len(out) >= limit:
            break
    return out


def search(uid, query, limit=15, from_person=None, since=None, until=None, channel_id=None):
    """Search Teams messages (chats, and channels you can read). Optional filters: sender name/email contains
    `from_person`, sent on/after `since`, on/before `until` (YYYY-MM-DD), in `channel_id`. Filters are applied to the
    results Microsoft returns, so broad words with narrow filters may find fewer matches."""
    for v, what in ((since, "since"), (until, "until")):
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
            raise tools.ToolError(f"'{what}' must look like 2026-10-22.")
    filtered = bool(from_person or since or until or channel_id)
    res = microsoft.graph(uid, "POST", "/search/query", {"requests": [{
        "entityTypes": ["chatMessage"], "query": {"queryString": str(query)[:200]}, "from": 0, "size": 25 if filtered else min(limit, 25)}]})
    out = []
    who = str(from_person or "").strip().lower()
    for container in (res.get("value") or [{}])[0].get("hitsContainers", []):
        for hit in container.get("hits", []):
            r = hit.get("resource") or {}
            sender = (r.get("from") or {}).get("emailAddress") or {}
            chan = r.get("channelIdentity") or {}
            item = {"id": r.get("id"), "chat_id": r.get("chatId"), "team_id": chan.get("teamId"), "channel_id": chan.get("channelId"),
                    "from": sender.get("name", ""), "from_email": sender.get("address", ""), "at": r.get("createdDateTime", ""),
                    "text": html.unescape(re.sub(r"<[^>]+>", "", hit.get("summary") or ""))[:400], "link": r.get("webLink")}
            if who and who not in item["from"].lower() and who not in (item["from_email"] or "").lower():
                continue
            if since and item["at"][:10] < since or until and item["at"][:10] > until:
                continue
            if channel_id and item["channel_id"] != channel_id:
                continue
            out.append(item)
    return out[:limit]


def send_chat(uid, chat_id, text):
    m = microsoft.graph(uid, "POST", f"/chats/{_q(_chat_id(chat_id))}/messages",
                        {"body": {"contentType": "text", "content": text[:20000]}})
    if not m.get("id"):
        raise tools.ToolError("Teams didn't confirm the message; it was not sent.")
    return m


def chat_with(uid, email):
    """The 1:1 chat with a colleague (created by Teams if it doesn't exist yet)."""
    people = common.emails_in(email)
    if len(people) != 1:
        raise tools.ToolError("Give one colleague's work email address.")
    me = microsoft.graph(uid, "GET", "/me", params={"$select": "id"})
    bind = "https://graph.microsoft.com/v1.0/users('{}')"
    chat = microsoft.graph(uid, "POST", "/chats", {"chatType": "oneOnOne", "members": [
        {"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"], "user@odata.bind": bind.format(me["id"])},
        {"@odata.type": "#microsoft.graph.aadUserConversationMember", "roles": ["owner"],
         "user@odata.bind": bind.format(urllib.parse.quote(people[0]))}]})
    if not chat.get("id"):
        raise tools.ToolError("Teams couldn't open a chat with that person.")
    return chat["id"]


def teams_and_channels(uid):
    teams = microsoft.graph(uid, "GET", "/me/joinedTeams", params={"$select": "id,displayName"}).get("value", [])
    out = []
    for t in teams[:20]:
        chans = microsoft.graph(uid, "GET", f"/teams/{_q(t['id'])}/channels", params={"$select": "id,displayName"}).get("value", [])
        out.append({"team_id": t["id"], "team": t.get("displayName"), "channels": [{"id": c["id"], "name": c.get("displayName")} for c in chans]})
    return out


def _team_ids(team_id, channel_id):
    if not GUID.fullmatch(str(team_id or "")) or not CHAT_ID.fullmatch(str(channel_id or "")):
        raise tools.ToolError("Use a team id and channel id from teams_channels.")
    return _q(team_id), _q(channel_id)


def channel_messages(uid, team_id, channel_id, limit=20):
    t, c = _team_ids(team_id, channel_id)
    res = microsoft.graph(uid, "GET", f"/teams/{t}/channels/{c}/messages", params={"$top": str(min(limit, 50))})
    return [_msg(m) for m in res.get("value", []) if m.get("messageType") == "message"]


def post_channel(uid, team_id, channel_id, text):
    t, c = _team_ids(team_id, channel_id)
    m = microsoft.graph(uid, "POST", f"/teams/{t}/channels/{c}/messages", {"body": {"contentType": "text", "content": text[:20000]}})
    if not m.get("id"):
        raise tools.ToolError("Teams didn't confirm the message; it was not posted.")
    return m


def set_auto_replies(uid, enable, internal="", external="", start=None, end=None, audience="all"):
    """Outlook automatic replies (scheduled when start/end are given)."""
    setting = {"status": "disabled"}
    if enable:
        setting = {"status": "scheduled" if start and end else "alwaysEnabled", "externalAudience": audience,
                   "internalReplyMessage": internal[:8000], "externalReplyMessage": (external or internal)[:8000]}
        if start and end:
            setting["scheduledStartDateTime"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}
            setting["scheduledEndDateTime"] = {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}
    microsoft.graph(uid, "PATCH", "/me/mailboxSettings", {"automaticRepliesSetting": setting})
    return microsoft.graph(uid, "GET", "/me/mailboxSettings/automaticRepliesSetting")
