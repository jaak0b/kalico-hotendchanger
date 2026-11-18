#!/usr/bin/env python3
# Hotendchanger filament service: per-tool filament library for Moonraker
import argparse
import asyncio
import logging
import os
import subprocess
import uuid

import filament_library as lib
from moonraker_agent import ConnectionLost, MoonrakerAgent, RpcError

PROJECT_URL = "https://github.com/jaak0b/kalico-hotendchanger"
RECONNECT_DELAY_MIN = 1.0
RECONNECT_DELAY_MAX = 30.0


def service_version():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = subprocess.run(
            ["git", "-C", repo, "describe", "--always", "--dirty"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        logging.warning("Cannot read the version from git in %s: %s", repo, e)
        return "unknown"
    return result.stdout.strip()


class FilamentService:
    def __init__(self, socket_path):
        self.store = None
        self.store_error = None
        self.tools = None
        self.lock = asyncio.Lock()
        self.reply_tasks = set()
        self.agent = MoonrakerAgent(
            socket_path,
            {
                "client_name": lib.AGENT_NAME,
                "version": service_version(),
                "type": "agent",
                "url": PROJECT_URL,
            },
            self.handle_request,
            self.handle_notification,
            self.on_connected,
            RECONNECT_DELAY_MIN,
            RECONNECT_DELAY_MAX,
        )

    async def on_connected(self):
        async with self.lock:
            self.tools = None
            await self.agent.call(
                "connection.register_remote_method",
                {"method_name": lib.CONSOLE_REMOTE_METHOD},
            )
            await self._load_store()
            info = await self.agent.call("server.info")
            klippy_state = info.get("klippy_state")
            if klippy_state == "ready":
                await self._subscribe()
            else:
                logging.info(
                    "Klipper state is %r; waiting for it to become ready",
                    klippy_state,
                )

    async def _read_namespace(self, namespace):
        listing = await self.agent.call("server.database.list")
        if namespace not in listing.get("namespaces", []):
            return {}
        result = await self.agent.call(
            "server.database.get_item", {"namespace": namespace}
        )
        value = result.get("value")
        if not isinstance(value, dict):
            raise lib.LibraryError(
                "Moonraker database namespace '%s' does not hold an object."
                % (namespace,)
            )
        return value

    async def _load_store(self):
        try:
            raw = (await self._read_namespace(lib.STORE_NAMESPACE)).get(
                lib.STORE_KEY
            )
            store, migrated = lib.load_store(raw)
        except lib.LibraryError as e:
            self.store = None
            self.store_error = str(e)
            logging.error("%s", e)
            return
        if migrated:
            await self._write_store(store)
            logging.info(
                "Converted the filament library to format version %d",
                lib.STORE_VERSION,
            )
        self.store = store
        self.store_error = None

    def _require_store(self):
        if self.store is None:
            raise lib.LibraryError(
                self.store_error or "The filament library is not loaded yet."
            )
        return self.store

    async def _subscribe(self):
        result = await self.agent.call(
            "printer.objects.subscribe",
            {"objects": {lib.STATUS_OBJECT: [lib.STATUS_TOOLS_FIELD]}},
        )
        status = result.get("status", {}).get(lib.STATUS_OBJECT)
        if status is None:
            self.tools = None
            logging.error(
                "Klipper reports no [%s] section, so no tools are known."
                " Configure the hotendchanger plugin, then restart Klipper.",
                lib.STATUS_OBJECT,
            )
            return
        await self._set_tools(status.get(lib.STATUS_TOOLS_FIELD))

    async def _set_tools(self, status_tools):
        try:
            tools = lib.parse_status_tools(status_tools)
        except lib.LibraryError as e:
            self.tools = None
            logging.error("%s", e)
            return
        if tools == self.tools:
            return
        self.tools = tools
        logging.info("Configured tools: %s", tools)
        try:
            await self._publish()
        finally:
            await self._announce_change()

    async def _publish(self):
        if self.tools is None:
            logging.info("Klipper is not ready; lane_data left unchanged")
            return
        store = self._require_store()
        desired = lib.build_lane_data(store, self.tools)
        current = await self._read_namespace(lib.LANE_DATA_NAMESPACE)
        writes, deletes = lib.lane_data_changes(current, desired)
        for key in sorted(writes):
            await self.agent.call(
                "server.database.post_item",
                {
                    "namespace": lib.LANE_DATA_NAMESPACE,
                    "key": [key],
                    "value": writes[key],
                },
            )
        for key in deletes:
            await self.agent.call(
                "server.database.delete_item",
                {"namespace": lib.LANE_DATA_NAMESPACE, "key": [key]},
            )
        if writes or deletes:
            logging.info(
                "lane_data updated: wrote %s, deleted %s",
                sorted(writes) or "nothing",
                deletes or "nothing",
            )

    async def _write_store(self, store):
        await self.agent.call(
            "server.database.post_item",
            {
                "namespace": lib.STORE_NAMESPACE,
                "key": lib.STORE_KEY,
                "value": store,
            },
        )

    async def _save(self, new_store):
        await self._write_store(new_store)
        self.store = new_store
        try:
            await self._publish()
        except (RpcError, lib.LibraryError) as e:
            raise lib.LibraryError(
                "The change was saved, but updating lane_data failed: %s" % (e,)
            )
        finally:
            await self._announce_change()

    async def _announce_change(self):
        # Moonraker relays an agent event to every other client as
        # notify_agent_event (moonraker/components/extensions.py:83-101).
        try:
            await self.agent.call("connection.send_event", {"event": lib.CHANGE_EVENT})
        except RpcError as e:
            logging.error(
                "The filament change was saved, but open web pages could not"
                " be told to refresh: %s",
                e.message,
            )

    async def handle_request(self, method, params):
        # Moonraker's HTTP route replaces a JSON-RPC error's message with a
        # generic one (moonraker/components/application.py:717-721), so every
        # failure is answered as a successful reply carrying "error".
        async with self.lock:
            try:
                new_store, payload = lib.run_api_method(
                    self._require_store(),
                    self.tools,
                    method,
                    params,
                    uuid.uuid4().hex,
                )
                if new_store is not None:
                    await self._save(new_store)
                return lib.api_result(payload)
            except lib.LibraryError as e:
                return lib.api_error(str(e))
            except RpcError as e:
                return lib.api_error("Moonraker request failed: %s" % (e.message,))
            except (ConnectionLost, asyncio.CancelledError):
                raise
            except Exception as e:
                logging.exception("Request %s failed", method)
                return lib.api_error(
                    "Internal error in the filament service while handling"
                    " %s: %s: %s. Check the service log with: journalctl -u %s"
                    % (method, type(e).__name__, e, lib.SERVICE_UNIT)
                )

    async def handle_notification(self, method, params):
        async with self.lock:
            if method == "notify_klippy_ready":
                await self._subscribe()
            elif method == "notify_klippy_disconnected":
                self.tools = None
            elif method == "notify_klippy_shutdown":
                self.tools = None
            elif method == "notify_status_update":
                status = params[0] if isinstance(params, list) and params else {}
                fields = status.get(lib.STATUS_OBJECT, {})
                if lib.STATUS_TOOLS_FIELD in fields:
                    await self._set_tools(fields[lib.STATUS_TOOLS_FIELD])
            elif method == lib.CONSOLE_REMOTE_METHOD:
                await self._run_console(params)
            else:
                # Moonraker broadcasts every server notification to all
                # connected clients (moonraker/components/websockets.py:203-215),
                # agents included; only the ones above concern this service.
                logging.debug("Ignoring notification %s", method)

    async def _run_console(self, params):
        request = params.get("request") if isinstance(params, dict) else None
        if not isinstance(request, str):
            logging.error(
                "Console command without a request id: %r. The hotendchanger"
                " plugin and this service are from different versions.",
                request,
            )
            request = None
        try:
            if not isinstance(params, dict):
                raise lib.LibraryError("The console command arrived without parameters.")
            new_store, text = lib.run_console_command(
                self._require_store(),
                self.tools,
                params.get("command"),
                params.get("params"),
                uuid.uuid4().hex,
            )
            if new_store is not None:
                await self._save(new_store)
            kind = lib.REPLY_INFO
        except lib.LibraryError as e:
            kind, text = lib.REPLY_ERROR, str(e)
        except RpcError as e:
            kind, text = lib.REPLY_ERROR, "Moonraker request failed: %s" % (e.message,)
        task = asyncio.ensure_future(self._reply(kind, text, request))
        self.reply_tasks.add(task)
        task.add_done_callback(self.reply_tasks.discard)

    async def _reply(self, kind, text, request):
        # printer.gcode.script waits for the gcode mutex, so it runs outside
        # the ordered message worker.
        try:
            await self.agent.call(
                "printer.gcode.script",
                {"script": lib.reply_script(kind, text, request)},
            )
        except (RpcError, ConnectionLost) as e:
            logging.error("Could not print the console reply %r: %s", text, e)


async def run(socket_path):
    service = FilamentService(socket_path)
    await service.agent.run_forever()


def main():
    parser = argparse.ArgumentParser(
        description="Per-tool filament library for hotendchanger printers,"
        " published to Moonraker's lane_data namespace."
    )
    parser.add_argument(
        "--socket",
        required=True,
        help="path to Moonraker's unix socket, <printer_data>/comms/moonraker.sock",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run(args.socket))


if __name__ == "__main__":
    main()
