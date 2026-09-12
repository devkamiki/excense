"""Headless smoke test of the sync engine against a real Radicale in-memory
store and a fake Graph client (no network)."""
import logging

logging.basicConfig(level=logging.CRITICAL)

from excense.bridge.engine import (
    HANDLERS,
    State,
    _collection,
    _items,
    _sync_kind,
    _text,
    _uid,
    _upload,
    load_storage,
)
from excense.config import Settings

USER = "excense"


class FakeGraph:
    def __init__(self):
        self.contacts = [
            {
                "id": "CNT-42",
                "displayName": "Ada Lovelace",
                "givenName": "Ada",
                "surname": "Lovelace",
                "emailAddresses": [{"address": "ada@example.com"}],
                "categories": [],
            }
        ]
        self.events = [
            {
                "id": "EVT-1",
                "subject": "Standup",
                "isAllDay": False,
                "start": {"dateTime": "2026-06-01T09:00:00.0000000", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-01T09:30:00.0000000", "timeZone": "UTC"},
                "location": {"displayName": "Lobby"},
                "bodyPreview": "",
                "categories": [],
            }
        ]
        self.lists = [{"id": "L1", "displayName": "Work"}]
        self.tasks = [
            {
                "id": "T-9",
                "title": "Draft report",
                "status": "notStarted",
                "importance": "normal",
                "dueDateTime": {"dateTime": "2026-07-01T17:00:00.0000000", "timeZone": "UTC"},
                "categories": [],
            }
        ]
        self.posted = []
        self.patched = []
        self.deleted = []

    def list_all(self, path, params=None):
        self.paths = getattr(self, "paths", [])
        self.paths.append(path)
        if path == "/me/contacts":
            return list(self.contacts)
        if path == "/me/calendar/events":
            return list(self.events)
        if path == "/me/todo/lists":
            return list(self.lists)
        if path.startswith("/me/todo/lists/") and path.endswith("/tasks"):
            return list(self.tasks)
        raise AssertionError(f"unexpected list_all path: {path}")

    def post(self, path, payload):
        self.posted.append((path, payload))
        out = dict(payload)
        out["id"] = f"NEW-{len(self.posted)}"
        return out

    def patch(self, path, payload):
        self.patched.append((path, payload))
        return dict(payload)

    def delete(self, path):
        self.deleted.append(path)


def make_env(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=5232,
        username=USER,
        _env_file=None,
    )
    storage = load_storage(settings)
    state = State(settings.state_db_path)
    return settings, storage, state


def cycle(storage, state, graph):
    with storage.acquire_lock("w"):
        for kind, handler in HANDLERS.items():
            _sync_kind(storage, state, graph, handler, make_settings())

def make_settings():
    return Settings(_env_file=None)


def local_texts(storage, path):
    collection = _collection(storage, f"/{USER}/{path}")
    return {_uid(it): _text(it) for it in _items(collection)} if collection else {}


def test_import_three_kinds(tmp_path):
    settings, storage, state = make_env(tmp_path)
    graph = FakeGraph()
    cycle(storage, state, graph)

    contacts = local_texts(storage, "contacts")
    events = local_texts(storage, "calendar")
    tasks = local_texts(storage, "todo/L1")

    assert set(contacts) == {"CNT-42"}
    assert set(events) == {"EVT-1"}
    assert set(tasks) == {"T-9"}
    assert state.find_by_graph("contact", "CNT-42") == "CNT-42"
    assert state.find_by_graph("task", "T-9") == "T-9"


def test_remote_delete_propagates(tmp_path):
    settings, storage, state = make_env(tmp_path)
    graph = FakeGraph()
    cycle(storage, state, graph)

    graph.contacts.clear()
    cycle(storage, state, graph)

    assert local_texts(storage, "contacts") == {}
    assert state.find_by_graph("contact", "CNT-42") is None


def test_local_create_propagates(tmp_path):
    settings, storage, state = make_env(tmp_path)
    graph = FakeGraph()
    cycle(storage, state, graph)

    collection = _collection(storage, "/excense/contacts")
    _upload(
        collection,
        "LOCAL-1",
        "\n".join(
            [
                "BEGIN:VCARD",
                "VERSION:3.0",
                "UID:LOCAL-1",
                "FN:Local Contact",
                "N:Contact;Local;;;",
                "EMAIL:local@example.com",
                "END:VCARD",
            ]
        ),
    )
    cycle(storage, state, graph)

    assert any(p == "/me/contacts" and p2["displayName"] == "Local Contact" for p, p2 in graph.posted)
    assert state.find_by_uid("contact", "LOCAL-1") is not None


def test_local_delete_propagates(tmp_path):
    settings, storage, state = make_env(tmp_path)
    graph = FakeGraph()
    cycle(storage, state, graph)

    collection = _collection(storage, "/excense/contacts")
    collection.delete("CNT-42")
    cycle(storage, state, graph)

    assert state.find_by_graph("contact", "CNT-42") is None
    assert "/me/contacts/CNT-42" in graph.deleted
    assert local_texts(storage, "contacts") == {}