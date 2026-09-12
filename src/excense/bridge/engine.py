"""Two-way sync between Microsoft Graph and a Radicale CalDAV/CardDAV store.

One Graph object maps to one calendar/contact/task item in Radicale. An sqlite
state file keeps three facts per item: Graph id <-> local uid, the last
content hash we pushed, and which collection it lives in.

Each cycle (per kind):

1. pull   - apply remote changes, but never clobber a local item that has
            unsynced edits (those win locally and get pushed in step 3).
2. delete - drop local items whose Graph counterpart vanished remotely.
3. push   - create/update/delete local changes, using last-push hashes to
            decide what changed.

The Radicale storage (v3.8 API) manages a single global read/write lock, so
the whole cycle runs inside ``storage.acquire_lock("w")``.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from excense import ExcenseError
from excense.auth import MsAuth
from excense.bridge.convert import (
    contact_from_vcf,
    contact_to_vcf,
    event_from_ics,
    event_to_ics,
    sha1,
    task_from_vtodo,
    task_to_vtodo,
)
from excense.config import Settings
from excense.graph.client import GraphClient, GraphError
from excense.radconf import build_radicale_config

log = logging.getLogger("excense.bridge")

UTC = timezone.utc


# --------------------------------------------------------------------- state


class State:
    def __init__(self, db_path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                kind         TEXT NOT NULL,
                graph_id     TEXT NOT NULL,
                uid          TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                coll         TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (kind, uid),
                UNIQUE (kind, graph_id)
            );
            CREATE TABLE IF NOT EXISTS snapshot (
                kind  TEXT NOT NULL,
                uid   TEXT NOT NULL,
                graph_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (kind, uid)
            );
            """
        )
        self.conn.commit()

    def find_by_graph(self, kind: str, gid: str) -> str | None:
        row = self.conn.execute(
            "SELECT uid FROM items WHERE kind=? AND graph_id=?", (kind, gid)
        ).fetchone()
        return row[0] if row else None

    def find_by_uid(self, kind: str, uid: str) -> tuple[str, str, str] | None:
        row = self.conn.execute(
            "SELECT graph_id, content_hash, coll FROM items WHERE kind=? AND uid=?",
            (kind, uid),
        ).fetchone()
        return row if row else None

    def all_rows(self, kind: str) -> list[tuple[str, str, str, str]]:
        rows = self.conn.execute(
            "SELECT graph_id, uid, content_hash, coll FROM items WHERE kind=?", (kind,)
        )
        return [tuple(r) for r in rows.fetchall()]

    def upsert(self, kind, gid, uid, content_hash, coll=""):
        self.conn.execute(
            """INSERT INTO items(kind, graph_id, uid, content_hash, coll)
               VALUES(?,?,?,?,?)
               ON CONFLICT(kind, uid) DO UPDATE SET
                 graph_id=excluded.graph_id,
                 content_hash=excluded.content_hash,
                 coll=excluded.coll""",
            (kind, gid, uid, content_hash, coll),
        )
        self.conn.commit()

    def set_hash(self, kind, uid, content_hash):
        self.conn.execute(
            "UPDATE items SET content_hash=? WHERE kind=? AND uid=?",
            (content_hash, kind, uid),
        )
        self.conn.commit()

    def delete_by_uid(self, kind, uid):
        self.conn.execute("DELETE FROM items WHERE kind=? AND uid=?", (kind, uid))
        self.conn.commit()

    def snapshot_upsert(self, kind, uid, gid, content_hash):
        self.conn.execute(
            """INSERT INTO snapshot(kind, uid, graph_id, content_hash)
               VALUES(?,?,?,?)
               ON CONFLICT(kind, uid) DO UPDATE SET
                 graph_id=excluded.graph_id,
                 content_hash=excluded.content_hash""",
            (kind, uid, gid or "", content_hash),
        )
        self.conn.commit()

    def snapshot_uids(self, kind):
        rows = self.conn.execute("SELECT uid FROM snapshot WHERE kind=?", (kind,))
        return {r[0] for r in rows.fetchall()}

    def snapshot_drop(self, kind, uid):
        self.conn.execute("DELETE FROM snapshot WHERE kind=? AND uid=?", (kind, uid))
        self.conn.commit()


# ------------------------------------------------------------ radicale api


def _is_collection(obj) -> bool:
    from radicale import storage as rs

    return isinstance(obj, rs.BaseCollection)


def _collection(storage, href: str):
    """Find an existing collection (href has a leading slash). None if absent."""
    for found in storage.discover(href, depth="0"):
        if _is_collection(found):
            return found
    return None


def _get_or_create_collection(storage, href: str):
    collection = _collection(storage, href)
    if collection is not None:
        return collection
    storage.create_collection(href)
    return _collection(storage, href)


def _items(collection) -> list:
    return list(collection.get_all()) if collection else []


def _uid(item):
    return getattr(item, "uid", None)


def _text(item):
    return item.serialize()


def _upload(coll, uid: str, content: str):
    from radicale.item import Item

    item = Item(collection=coll, href=uid, text=content)
    coll.upload(uid, item)


def _item_lookup(collection, uid: str) -> str | None:
    """Return serialized item text for a uid, or None."""
    for item in _items(collection):
        if _uid(item) == uid:
            return _text(item)
    return None


# ----------------------------------------------------------------- handlers


@dataclass
class Handler:
    kind: str
    base: str  # collection name under the user: calendar / contacts / todo

    def fetch(self, graph: GraphClient, settings: Settings) -> tuple[list[str], list[tuple[str, dict]]]:
        """Return (collection suffixes, [(suffix, item)]); suffix '' unless tasks."""
        raise NotImplementedError

    def to_local(self, item: dict, uid: str) -> str:
        raise NotImplementedError

    def from_local(self, text: str) -> tuple[dict, str | None]:
        raise NotImplementedError

    def upsert(self, graph: GraphClient, payload: dict, gid: str | None, suffix: str) -> str | None:
        raise NotImplementedError

    def delete(self, graph: GraphClient, gid: str, suffix: str) -> None:
        raise NotImplementedError


class EventHandler(Handler):
    SELECT = "id,subject,start,end,isAllDay,location,bodyPreview,categories"

    def fetch(self, graph, settings):
        now = datetime.now(UTC)
        past = (now - timedelta(days=settings.calendar_past_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        future = (now + timedelta(days=settings.calendar_future_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": self.SELECT,
            "$filter": f"start/dateTime ge '{past}' and start/dateTime le '{future}'",
            "$top": "500",
        }
        try:
            items = graph.list_all("/me/calendar/events", params=params)
        except GraphError as err:
            if err.status != 400:
                raise
            items = graph.list_all("/me/calendar/events", params={"$select": self.SELECT, "$top": "500"})
        return [""], [("", i) for i in items]

    def to_local(self, item, uid):
        return event_to_ics(item, uid)

    def from_local(self, text):
        return event_from_ics(text)

    def upsert(self, graph, payload, gid, suffix=""):
        path = f"/me/calendar/events/{gid}" if gid else "/me/calendar/events"
        result = graph.patch(path, payload) if gid else graph.post(path, payload)
        return result.get("id")

    def delete(self, graph, gid, suffix=""):
        graph.delete(f"/me/calendar/events/{gid}")


class ContactHandler(Handler):
    SELECT = (
        "id,displayName,givenName,surname,emailAddresses,businessPhones,"
        "mobilePhone,homePhones,personalNotes,companyName,jobTitle,birthday,categories"
    )

    def fetch(self, graph, settings):
        items = graph.list_all("/me/contacts", params={"$select": self.SELECT, "$top": "500"})
        return [""], [("", i) for i in items]

    def to_local(self, item, uid):
        return contact_to_vcf(item, uid)

    def from_local(self, text):
        return contact_from_vcf(text)

    def upsert(self, graph, payload, gid, suffix=""):
        path = f"/me/contacts/{gid}" if gid else "/me/contacts"
        result = graph.patch(path, payload) if gid else graph.post(path, payload)
        return result.get("id")

    def delete(self, graph, gid, suffix=""):
        graph.delete(f"/me/contacts/{gid}")


class TaskHandler(Handler):
    TASK_SELECT = (
        "id,title,status,importance,startedDateTime,dueDateTime,"
        "completedDateTime,body,categories"
    )

    def fetch(self, graph, settings):
        lists = graph.list_all("/me/todo/lists", params={"$select": "id,displayName", "$top": "500"})
        suffixes = [lst["id"] for lst in lists]
        items: list[tuple[str, dict]] = []
        for lst in lists:
            tasks = graph.list_all(
                f"/me/todo/lists/{lst['id']}/tasks",
                params={"$select": self.TASK_SELECT, "$top": "500"},
            )
            items += [(lst["id"], t) for t in tasks]
        return suffixes, items

    def to_local(self, item, uid):
        return task_to_vtodo(item, uid)

    def from_local(self, text):
        return task_from_vtodo(text)

    def upsert(self, graph, payload, gid, suffix):
        path = f"/me/todo/lists/{suffix}/tasks/{gid}" if gid else f"/me/todo/lists/{suffix}/tasks"
        result = graph.patch(path, payload) if gid else graph.post(path, payload)
        return result.get("id")

    def delete(self, graph, gid, suffix):
        graph.delete(f"/me/todo/lists/{suffix}/tasks/{gid}")


HANDLERS: dict[str, Handler] = {
    "event": EventHandler("event", "calendar"),
    "contact": ContactHandler("contact", "contacts"),
    "task": TaskHandler("task", "todo"),
}


# ------------------------------------------------------------------- pulls


def _pull(storage, state, kind, handler, items: list[tuple[str, dict]]):
    user = "excense"
    known = state.all_rows(kind)
    rows_by_gid = {r[0]: r for r in known}

    for suffix, item in items:
        gid = item["id"]
        path = f"/{user}/{handler.base}" + (f"/{suffix}" if suffix else "")
        collection = _collection(storage, path)
        row = rows_by_gid.get(gid)
        if row is None:
            if collection is not None and _item_lookup(collection, gid):
                continue  # imported item deleted locally; don't resurrect it
            uid = gid
            content = handler.to_local(item, uid)
            collection = _get_or_create_collection(storage, path)
            _upload(collection, uid, content)
            state.upsert(kind, gid, uid, sha1(content), suffix)
            continue
        uid = row[1]
        local = _item_lookup(collection, uid) if collection else None
        if local is None:
            continue  # deleted locally; push phase removes it remotely
        if sha1(local) == row[2]:
            content = handler.to_local(item, uid)
            if content != local:
                _upload(collection, uid, content)
                state.set_hash(kind, uid, sha1(content))


def _remote_deletions(storage, state, kind, handler, remote_gids: set[str]):
    user = "excense"
    for gid, uid, _h, suffix in state.all_rows(kind):
        if gid in remote_gids:
            continue
        path = f"/{user}/{handler.base}" + (f"/{suffix}" if suffix else "")
        collection = _collection(storage, path)
        if collection is not None and _item_lookup(collection, uid):
            collection.delete(uid)
        state.delete_by_uid(kind, uid)
        state.snapshot_drop(kind, uid)


# ------------------------------------------------------------------- pushes


def _push_collection(storage, state, graph, kind, handler, suffix):
    user = "excense"
    path = f"/{user}/{handler.base}" + (f"/{suffix}" if suffix else "")
    collection = _collection(storage, path)
    if collection is None:
        return
    local = {_uid(it): _text(it) for it in _items(collection)}
    local = {u: t for u, t in local.items() if u}

    for uid in state.snapshot_uids(kind):
        if uid in local:
            continue
        row = state.find_by_uid(kind, uid)
        if row:
            gid, _hash, coll = row
            handler.delete(graph, gid, coll)
        state.delete_by_uid(kind, uid)
        state.snapshot_drop(kind, uid)

    for uid, text in local.items():
        h = sha1(text)
        row = state.find_by_uid(kind, uid)
        if row is None:
            payload, _gid = handler.from_local(text)
            gid = handler.upsert(graph, payload, None, suffix)
            if gid:
                state.upsert(kind, gid, uid, h, suffix)
        elif row[1] != h:
            payload, _gid = handler.from_local(text)
            handler.upsert(graph, payload, row[0], suffix)
            state.set_hash(kind, uid, h)
        row = state.find_by_uid(kind, uid)
        state.snapshot_upsert(kind, uid, row[0] if row else "", h)


# -------------------------------------------------------------------- cycle


def _sync_kind(storage, state, graph, handler, settings) -> dict:
    suffixes, items = handler.fetch(graph, settings)
    remote_gids = {i["id"] for _s, i in items}
    before_pull = {r[1] for r in state.all_rows(handler.kind)}

    items_by_suffix: dict[str, list[tuple[str, dict]]] = {}
    for suffix, item in items:
        items_by_suffix.setdefault(suffix, []).append((suffix, item))

    _pull(storage, state, handler.kind, handler, items)
    _remote_deletions(storage, state, handler.kind, handler, remote_gids)

    suffixes_in_use = set(suffixes) | {row[3] for row in state.all_rows(handler.kind)}
    for suffix in suffixes_in_use:
        _push_collection(storage, state, graph, handler.kind, handler, suffix)

    return {
        "remote": len(items),
        "pulled": len({r[1] for r in state.all_rows(handler.kind)} - before_pull),
    }


def load_storage(settings: Settings):
    """Radicale Storage backed by the generated config file."""
    from radicale import config as rad_config
    from radicale import storage as rad_storage

    conf = rad_config.load(((build_radicale_config(settings), False),))
    return rad_storage.load(conf)


def sync_once(settings: Settings, auth: MsAuth | None = None) -> dict:
    """Run one full sync cycle. Returns per-kind stats."""
    auth = auth or MsAuth(settings)
    graph = GraphClient(auth, settings.graph_api)
    storage = load_storage(settings)
    state = State(settings.state_db_path)

    result: dict[str, dict] = {}
    with storage.acquire_lock("w"):
        for kind, handler in HANDLERS.items():
            result[kind] = _sync_kind(storage, state, graph, handler, settings)
    return result


def sync_loop(settings: Settings, auth: MsAuth | None = None) -> None:
    """Run sync every `sync_interval_seconds`, forever (the Docker CMD)."""
    import time

    auth = auth or MsAuth(settings)
    interval = settings.sync_interval_seconds
    log.info("sync loop started: every %s s", interval)
    while True:
        try:
            stats = sync_once(settings, auth)
            log.info("cycle done: %s", stats)
        except ExcenseError as err:
            log.error("cycle failed: %s", err)
        time.sleep(interval)