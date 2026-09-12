import vobject

from excense.bridge.convert import (
    contact_from_vcf,
    contact_to_vcf,
    event_from_ics,
    event_to_ics,
    task_from_vtodo,
    task_to_vtodo,
)


def graph_event(**over):
    ev = {
        "id": "AAMk-123",
        "subject": "Sync planning",
        "isAllDay": False,
        "start": {"dateTime": "2026-06-01T09:30:00.0000000", "timeZone": "Europe/Berlin"},
        "end": {"dateTime": "2026-06-01T10:30:00.0000000", "timeZone": "Europe/Berlin"},
        "location": {"displayName": "Conference Room 1"},
        "bodyPreview": "Bring the whiteboard.",
        "categories": ["work", "meeting"],
    }
    ev.update(over)
    return ev


def test_event_round_trip_preserves_fields():
    event = graph_event()
    serialized = event_to_ics(event, uid="AAMk-123")
    payload, gid = event_from_ics(serialized)

    assert gid == "AAMk-123"
    assert payload["subject"] == "Sync planning"
    assert payload["location"]["displayName"] == "Conference Room 1"
    assert payload["body"]["content"] == "Bring the whiteboard."
    assert payload["categories"] == ["work", "meeting"]
    assert payload["start"]["timeZone"] == "UTC"
    assert not payload["isAllDay"]


def test_event_all_day_round_trip():
    event = graph_event(
        isAllDay=True,
        start={"dateTime": "2026-06-01", "timeZone": "UTC"},
        end={"dateTime": "2026-06-02", "timeZone": "UTC"},
    )
    payload, _ = event_from_ics(event_to_ics(event, uid=event["id"]))
    assert payload["isAllDay"] is True
    assert payload["start"]["dateTime"] == "2026-06-01"


def test_contact_round_trip():
    contact = {
        "id": "CNT-42",
        "displayName": "Ada Lovelace",
        "givenName": "Ada",
        "surname": "Lovelace",
        "emailAddresses": [{"address": "ada@example.com", "name": "Ada", "type": "work"}],
        "businessPhones": ["+1 555 0100"],
        "mobilePhone": "+1 555 0111",
        "companyName": "Analytical Engines Ltd",
        "jobTitle": "Programmer",
        "personalNotes": "First programmer.",
        "categories": ["colleagues"],
    }
    serialized = contact_to_vcf(contact, uid="CNT-42")
    payload, gid = contact_from_vcf(serialized)

    assert gid == "CNT-42"
    assert payload["displayName"] == "Ada Lovelace"
    assert payload["givenName"] == "Ada"
    assert payload["surname"] == "Lovelace"
    assert payload["emailAddresses"][0]["address"] == "ada@example.com"
    assert payload["businessPhones"] == ["+1 555 0100"]
    assert payload["mobilePhone"] == "+1 555 0111"
    assert payload["companyName"] == "Analytical Engines Ltd"
    assert payload["personalNotes"] == "First programmer."


def test_task_round_trip():
    task = {
        "id": "TSK-7",
        "title": "File the VAT return",
        "status": "inProgress",
        "importance": "high",
        "startedDateTime": {"dateTime": "2026-05-01T08:00:00.0000000", "timeZone": "UTC"},
        "dueDateTime": {"dateTime": "2026-05-15T17:00:00.0000000", "timeZone": "UTC"},
        "body": {"content": "Ask accounting first.", "contentType": "text"},
        "categories": ["finance"],
    }
    serialized = task_to_vtodo(task, uid="TSK-7")
    payload, gid = task_from_vtodo(serialized)

    assert gid == "TSK-7"
    assert payload["title"] == "File the VAT return"
    assert payload["status"] == "inProgress"
    assert payload["importance"] == "high"
    assert payload["dueDateTime"]["timeZone"] == "UTC"
    assert payload["body"]["content"] == "Ask accounting first."


def test_serialized_form_parses_as_one_component():
    cal = vobject.readOne(event_to_ics(graph_event(), uid="A"))
    assert cal.name == "VCALENDAR"