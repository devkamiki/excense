"""Conversions between Microsoft Graph resources and iCalendar/vCard text.

The gateway stores each Graph object as a CalDAV/CardDAV item. The Graph item
ID is kept in a custom property (`X-EXCENSE-GRAPHID`) so the two-way sync can
map local resources back to remote ones. All times funnel through UTC.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import vobject
from vobject.icalendar import utc as vobject_utc

GRAPHID_PROP = "x-excense-graphid"
SERVICE = "-//excense//Microsoft 365 Graph mirror//EN"

UTC = timezone.utc


def _vutc(dt: datetime) -> datetime:
    """Convert to UTC in vobject's native tzinfo so it serializes as 'Z'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(tzinfo=vobject_utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    m = re.match(r"^(.*?\.)(\d+)(.*)$", s)  # trim >6 fractional digits
    if m:
        s = m.group(1) + m.group(2)[:6] + m.group(3)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _graph_dt(start: dict | None) -> datetime | None:
    if not start:
        return None
    dt = _parse_datetime(start.get("dateTime"))
    if dt is None or dt.tzinfo is not None:
        return dt
    tz = start.get("timeZone")
    if tz and tz not in ("UTC", "Etc/UTC"):
        try:
            return dt.replace(tzinfo=ZoneInfo(tz))
        except ZoneInfoNotFoundError:
            pass
    return dt.replace(tzinfo=UTC)


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _component(serialized: str):
    return vobject.readOne(serialized)


def _prop(component, name: str):
    props = component.contents.get(name.lower())
    if props:
        return props[0].value
    return None


def _prop_types(prop) -> list[str]:
    """TYPE/PARAM values from a vobject property (string or list-tolerant)."""
    raw = getattr(prop, "type_param", None) or []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    types: list[str] = []
    for item in items:
        for tok in str(item).replace(";", " ").split():
            types.append(tok.upper())
    return types


# ------------------------------------------------------------------- events


def event_to_ics(event: dict, uid: str) -> str:
    cal = vobject.iCalendar()
    cal.add("prodid").value = SERVICE
    cal.add("version").value = "2.0"
    ve = cal.add("vevent")
    ve.add("uid").value = uid
    ve.add("dtstamp").value = _vutc(datetime.now(UTC))
    ve.add(GRAPHID_PROP).value = event["id"]
    ve.add("summary").value = event.get("subject") or ""

    start = _graph_dt(event.get("start"))
    end = _graph_dt(event.get("end"))
    if event.get("isAllDay"):
        if start:
            ve.add("dtstart").value = start.date()
        if end:
            ve.add("dtend").value = end.date()
    else:
        if start:
            ve.add("dtstart").value = _vutc(start)
        if end:
            ve.add("dtend").value = _vutc(end)

    location = (event.get("location") or {}).get("displayName")
    if location:
        ve.add("location").value = location
    if event.get("bodyPreview"):
        ve.add("description").value = event["bodyPreview"]
    if event.get("categories"):
        ve.add("categories").value = event["categories"]
    return cal.serialize()


def event_from_ics(serialized: str) -> tuple[dict, str | None]:
    """Local VEVENT -> Graph event payload (+ remote graph id if present)."""
    cal = _component(serialized)
    ev = cal.vevent
    payload: dict = {"isAllDay": False}

    summary = _prop(ev, "summary")
    if summary is not None:
        payload["subject"] = str(summary)

    dtstart = _prop(ev, "dtstart")
    dtend = _prop(ev, "dtend")
    if isinstance(dtstart, datetime) and isinstance(dtend, datetime):
        payload["start"] = {"dateTime": _fmt_utc(dtstart), "timeZone": "UTC"}
        payload["end"] = {"dateTime": _fmt_utc(dtend), "timeZone": "UTC"}
    elif isinstance(dtstart, datetime):
        payload["start"] = {"dateTime": _fmt_utc(dtstart), "timeZone": "UTC"}
    elif isinstance(dtstart, date):
        payload["isAllDay"] = True
        payload["start"] = {"dateTime": dtstart.isoformat(), "timeZone": "UTC"}
        payload["end"] = {
            "dateTime": (dtend or dtstart).isoformat(), "timeZone": "UTC"
        }

    location = _prop(ev, "location")
    if location is not None:
        payload["location"] = {"displayName": str(location)}
    description = _prop(ev, "description")
    if description is not None:
        payload["body"] = {"contentType": "text", "content": str(description)}
    categories = _prop(ev, "categories")
    if categories is not None:
        payload["categories"] = list(categories)

    return payload, _prop(ev, GRAPHID_PROP) and str(_prop(ev, GRAPHID_PROP))


# ----------------------------------------------------------------- contacts


def contact_to_vcf(contact: dict, uid: str) -> str:
    card = vobject.vCard()
    card.add("fn").value = (
        contact.get("displayName")
        or (f"{contact.get('givenName', '')} {contact.get('surname', '')}".strip())
        or (contact.get("emailAddresses") or [{}])[0].get("address")
        or "?"
    )
    card.add("n").value = vobject.vcard.Name(
        family=contact.get("surname") or "", given=contact.get("givenName") or ""
    )
    card.add("uid").value = uid
    card.add(GRAPHID_PROP).value = contact["id"]

    for i, mail in enumerate(contact.get("emailAddresses") or []):
        em = card.add("email")
        em.value = mail.get("address", "")
        em.type_param = (["PREF"] if i == 0 else []) + ["INTERNET"]

    for phone in contact.get("businessPhones") or []:
        tel = card.add("tel"); tel.value = phone; tel.type_param = ["WORK"]
    for phone in contact.get("mobilePhone") and [contact["mobilePhone"]] or []:
        tel = card.add("tel"); tel.value = phone; tel.type_param = ["CELL"]
    for phone in contact.get("homePhones") or []:
        tel = card.add("tel"); tel.value = phone; tel.type_param = ["HOME"]

    if contact.get("companyName"):
        card.add("org").value = [contact["companyName"]]
    if contact.get("jobTitle"):
        card.add("title").value = contact["jobTitle"]
    if contact.get("personalNotes"):
        card.add("note").value = contact["personalNotes"]
    if contact.get("birthday"):
        card.add("bday").value = date.fromisoformat(contact["birthday"][:10])
    if contact.get("categories"):
        card.add("categories").value = contact["categories"]
    return card.serialize()


def contact_from_vcf(serialized: str) -> tuple[dict, str | None]:
    card = _component(serialized)
    payload: dict = {}

    fn = _prop(card, "fn")
    if fn is not None:
        payload["displayName"] = str(fn)

    n = card.contents.get("n")
    if n:
        nm = n[0].value
        if getattr(nm, "given", None):
            payload["givenName"] = nm.given
        if getattr(nm, "family", None):
            payload["surname"] = nm.family

    emails = []
    for em in card.contents.get("email") or []:
        kinds = _prop_types(em)
        etype = "work" if "WORK" in kinds else "personal"
        emails.append({"address": str(em.value), "name": str(em.value), "type": etype})
    if emails:
        payload["emailAddresses"] = emails

    work, mobile, home = [], [], []
    for tel in card.contents.get("tel") or []:
        kinds = _prop_types(tel)
        if "MOBILE" in kinds or "CELL" in kinds:
            mobile.append(str(tel.value))
        elif "HOME" in kinds:
            home.append(str(tel.value))
        else:
            work.append(str(tel.value))
    if work:
        payload["businessPhones"] = work
    if mobile:
        payload["mobilePhone"] = mobile[0]
    if home:
        payload["homePhones"] = home

    org = _prop(card, "org")
    if org and list(org):
        payload["companyName"] = str(list(org)[0])
    title = _prop(card, "title")
    if title is not None:
        payload["jobTitle"] = str(title)
    note = _prop(card, "note")
    if note is not None:
        payload["personalNotes"] = str(note)
    bday = _prop(card, "bday")
    if isinstance(bday, date) and not isinstance(bday, datetime):
        payload["birthday"] = bday.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    gid = card.contents.get(GRAPHID_PROP)
    return payload, str(gid[0].value) if gid else None


# --------------------------------------------------------------------- tasks


_TASK_STATUS = {
    "notStarted": "NEEDS-ACTION",
    "inProgress": "IN-PROCESS",
    "completed": "COMPLETED",
    "postponed": "POSTPONED",
    "waitingOnOthers": "NEEDS-ACTION",
}
_TASK_STATUS_REV = {v: k for k, v in _TASK_STATUS.items()}


def task_to_vtodo(task: dict, uid: str) -> str:
    cal = vobject.iCalendar()
    cal.add("prodid").value = SERVICE
    cal.add("version").value = "2.0"
    vt = cal.add("vtodo")
    vt.add("uid").value = uid
    vt.add("dtstamp").value = _vutc(datetime.now(UTC))
    vt.add(GRAPHID_PROP).value = task["id"]
    vt.add("summary").value = task.get("title") or ""

    status = _TASK_STATUS.get(task.get("status", "notStarted"))
    vt.add("status").value = status
    if status == "COMPLETED":
        vt.add("percent-complete").value = "100"
    elif status == "IN-PROCESS":
        vt.add("percent-complete").value = "50"

    start = _graph_dt(task.get("startedDateTime"))
    due = _graph_dt(task.get("dueDateTime"))
    done = _graph_dt(task.get("completedDateTime"))
    if start:
        vt.add("dtstart").value = _vutc(start)
    if due:
        vt.add("due").value = _vutc(due)
    if done and status == "COMPLETED":
        vt.add("completed").value = _vutc(done)

    body = (task.get("body") or {}).get("content")
    if body:
        vt.add("description").value = body
    if task.get("categories"):
        vt.add("categories").value = task["categories"]
    importance = task.get("importance")
    if importance:
        vt.add("x-excense-importance").value = importance
    return cal.serialize()


def task_from_vtodo(serialized: str) -> tuple[dict, str | None]:
    cal = _component(serialized)
    vt = cal.vtodo
    payload: dict = {"status": "notStarted"}

    summary = _prop(vt, "summary")
    if summary is not None:
        payload["title"] = str(summary)

    status = _prop(vt, "status")
    if status is not None:
        status = str(status).upper()
        if status in _TASK_STATUS_REV:
            payload["status"] = _TASK_STATUS_REV[status]

    due = _prop(vt, "due")
    if isinstance(due, datetime):
        payload["dueDateTime"] = {"dateTime": _fmt_utc(due), "timeZone": "UTC"}
    start = _prop(vt, "dtstart")
    if isinstance(start, datetime):
        payload["startedDateTime"] = {"dateTime": _fmt_utc(start), "timeZone": "UTC"}
    done = _prop(vt, "completed")
    if isinstance(done, datetime):
        payload["completedDateTime"] = {"dateTime": _fmt_utc(done), "timeZone": "UTC"}

    description = _prop(vt, "description")
    if description is not None:
        payload["body"] = {"contentType": "text", "content": str(description)}
    categories = _prop(vt, "categories")
    if categories is not None:
        payload["categories"] = list(categories)
    importance = _prop(vt, "x-excense-importance")
    if importance is not None:
        payload["importance"] = str(importance).lower()

    gid = vt.contents.get(GRAPHID_PROP)
    return payload, str(gid[0].value) if gid else None