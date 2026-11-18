import asyncio
import os
import sys

import pytest

# The service runs as a script from its own directory and imports its
# siblings as top-level modules.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service")
)

import hotendchanger_filament  # noqa: E402
from moonraker_agent import RpcError  # noqa: E402

PLA = {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215}


class FakeMoonraker:
    def __init__(self, fail_method=None, failure=None, database=None):
        self.fail_method = fail_method
        self.failure = failure
        self.database = database or {}
        self.posted = []
        self.events = []

    async def call(self, method, params=None):
        if method == self.fail_method:
            raise self.failure
        if method == "server.database.list":
            return {"namespaces": sorted(self.database)}
        if method == "server.database.get_item":
            return {"value": self.database[params["namespace"]]}
        if method == "server.database.post_item":
            self.posted.append(params)
            return {}
        if method == "connection.send_event":
            self.events.append(params)
            return "ok"
        raise AssertionError("unexpected Moonraker call %s" % (method,))


def service_with(moonraker, store=None, tools=None):
    service = hotendchanger_filament.FilamentService("/unused.sock")
    service.store = store
    service.tools = tools
    service.agent.call = moonraker.call
    return service


def request(service, method, params):
    return asyncio.run(service.handle_request(method, params))


def test_success_reply_wraps_payload_in_result():
    service = service_with(
        FakeMoonraker(), {"filaments": {"pla": dict(PLA)}, "assignments": {}}, {"T0": 0}
    )
    assert request(service, "tools.assign", {"tool": 0, "filament_id": "pla", "color": "ff0000"}) == {
        "result": {"tool": 0, "filament_id": "pla", "color": "#FF0000"}
    }


def test_refused_request_replies_with_error_message():
    service = service_with(FakeMoonraker(), {"filaments": {}, "assignments": {}}, {"T0": 0})
    assert request(service, "tools.assign", {"tool": 4, "filament_id": None}) == {
        "error": "Tool number 4 is not configured in Klipper. Configured tool numbers: 0."
    }


def test_unknown_method_replies_with_error_message():
    service = service_with(FakeMoonraker(), {"filaments": {}, "assignments": {}})
    assert request(service, "filaments.purge", None) == {
        "error": "Unknown method 'filaments.purge'. Available methods:"
        " filaments.list, filaments.create, filaments.update,"
        " filaments.delete, tools.list, tools.assign, tools.set_color."
    }


def test_unloaded_library_replies_with_error_message():
    service = service_with(FakeMoonraker())
    assert request(service, "filaments.list", {}) == {
        "error": "The filament library is not loaded yet."
    }


def test_failed_moonraker_write_replies_with_error_message():
    moonraker = FakeMoonraker(
        "server.database.post_item", RpcError(-32603, "database is read only")
    )
    service = service_with(moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}})
    assert request(service, "filaments.delete", {"id": "pla"}) == {
        "error": "Moonraker request failed: database is read only"
    }


def test_internal_exception_is_logged_and_replied_as_error(caplog):
    moonraker = FakeMoonraker("server.database.post_item", KeyError("value"))
    service = service_with(moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}})
    assert request(service, "filaments.delete", {"id": "pla"}) == {
        "error": "Internal error in the filament service while handling"
        " filaments.delete: KeyError: 'value'. Check the service log with:"
        " journalctl -u hotendchanger-filament"
    }
    assert "Traceback" in caplog.text


def test_cancellation_is_not_turned_into_a_reply():
    moonraker = FakeMoonraker("server.database.post_item", asyncio.CancelledError())
    service = service_with(moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}})
    with pytest.raises(asyncio.CancelledError):
        request(service, "filaments.delete", {"id": "pla"})


def test_saved_change_is_announced_to_open_pages():
    moonraker = FakeMoonraker()
    service = service_with(
        moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}}, {"T0": 0}
    )
    request(service, "tools.assign", {"tool": 0, "filament_id": "pla", "color": "ff0000"})
    assert moonraker.events == [{"event": "library_changed"}]


def test_refused_change_is_not_announced():
    moonraker = FakeMoonraker()
    service = service_with(moonraker, {"filaments": {}, "assignments": {}}, {"T0": 0})
    request(service, "tools.assign", {"tool": 4, "filament_id": None})
    assert moonraker.events == []


def test_read_only_request_is_not_announced():
    moonraker = FakeMoonraker()
    service = service_with(
        moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}}, {"T0": 0}
    )
    request(service, "filaments.list", {})
    assert moonraker.events == []


def test_failed_announcement_still_reports_the_saved_change(caplog):
    moonraker = FakeMoonraker("connection.send_event", RpcError(-32601, "Method not found"))
    service = service_with(moonraker, {"filaments": {"pla": dict(PLA)}, "assignments": {}})
    assert request(service, "filaments.delete", {"id": "pla"}) == {
        "result": {"deleted": "pla"}
    }
    assert "Method not found" in caplog.text


def notify(service, method, params):
    asyncio.run(service.handle_notification(method, params))


def test_newly_known_tools_are_announced_to_open_pages():
    moonraker = FakeMoonraker()
    service = service_with(moonraker, {"filaments": {}, "assignments": {}})
    notify(
        service,
        "notify_status_update",
        [{"hotendchanger": {"tools": {"T0": {"number": 0}}}}, 1.0],
    )
    assert moonraker.events == [{"event": "library_changed"}]


def test_unchanged_tools_are_not_announced():
    moonraker = FakeMoonraker()
    service = service_with(moonraker, {"filaments": {}, "assignments": {}}, {"T0": 0})
    notify(
        service,
        "notify_status_update",
        [{"hotendchanger": {"tools": {"T0": {"number": 0}}}}, 1.0],
    )
    assert moonraker.events == []


def test_unversioned_library_is_converted_and_written_back():
    old = {
        "filaments": {"pla": {"name": "Basic PLA", "material": "PLA", "color": "#FFFFFF", "nozzle_temp": 215}},
        "assignments": {"0": "pla"},
    }
    moonraker = FakeMoonraker(database={"hotendchanger": {"library": old}})
    service = service_with(moonraker)
    asyncio.run(service._load_store())
    assert moonraker.posted == [
        {
            "namespace": "hotendchanger",
            "key": "library",
            "value": {
                "version": 2,
                "filaments": {
                    "pla": {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215}
                },
                "assignments": {"0": {"filament": "pla", "color": "#FFFFFF"}},
            },
        }
    ]


def test_current_library_is_loaded_without_writing():
    current = {
        "version": 2,
        "filaments": {"pla": {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215}},
        "assignments": {"0": {"filament": "pla", "color": "#00FF00"}},
    }
    moonraker = FakeMoonraker(database={"hotendchanger": {"library": current}})
    service = service_with(moonraker)
    asyncio.run(service._load_store())
    assert moonraker.posted == []


def test_library_of_unknown_version_is_refused_with_its_location():
    future = {"version": 3, "filaments": {}, "assignments": {}}
    moonraker = FakeMoonraker(database={"hotendchanger": {"library": future}})
    service = service_with(moonraker)
    asyncio.run(service._load_store())
    assert request(service, "filaments.list", {}) == {
        "error": "The filament library stored in Moonraker database namespace"
        " 'hotendchanger', key 'library', has format version 3, which this"
        " filament service does not know. Update the filament service to the"
        " version that wrote it, or restore the library from a database backup."
    }
