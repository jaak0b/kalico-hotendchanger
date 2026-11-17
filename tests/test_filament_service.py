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

PLA = {"name": "Basic PLA", "material": "PLA", "color": "#FFFFFF", "nozzle_temp": 215}


class FakeMoonraker:
    def __init__(self, fail_method=None, failure=None):
        self.fail_method = fail_method
        self.failure = failure
        self.posted = []

    async def call(self, method, params=None):
        if method == self.fail_method:
            raise self.failure
        if method == "server.database.list":
            return {"namespaces": []}
        if method == "server.database.post_item":
            self.posted.append(params)
            return {}
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
    assert request(service, "tools.assign", {"tool": 0, "filament_id": "pla"}) == {
        "result": {"tool": 0, "filament_id": "pla"}
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
        " filaments.delete, tools.list, tools.assign."
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
