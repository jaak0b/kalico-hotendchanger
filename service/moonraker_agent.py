# Moonraker agent connection over Moonraker's unix socket
import asyncio
import itertools
import json
import logging

# Moonraker frames every JSON-RPC message on its unix socket with a trailing
# ETX byte (moonraker/components/extensions.py:186 and :205-209).
ETX = b"\x03"
# Same stream buffer limit Moonraker uses for this socket
# (moonraker/components/extensions.py:29, :139-141).
STREAM_LIMIT = 20 * 1024 * 1024

RPC_INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.data = data


class ConnectionLost(Exception):
    pass


class MoonrakerAgent:
    def __init__(
        self,
        socket_path,
        identity,
        handle_request,
        handle_notification,
        on_connected,
        reconnect_delay_min,
        reconnect_delay_max,
    ):
        self.socket_path = socket_path
        self.identity = identity
        self.handle_request = handle_request
        self.handle_notification = handle_notification
        self.on_connected = on_connected
        self.reconnect_delay_min = reconnect_delay_min
        self.reconnect_delay_max = reconnect_delay_max
        self._writer = None
        self._write_lock = asyncio.Lock()
        self._pending = {}
        self._ids = itertools.count(1)

    async def run_forever(self):
        delay = self.reconnect_delay_min
        while True:
            identified = False
            try:
                identified = await self._session()
                logging.warning("Moonraker closed the connection")
            except (OSError, ConnectionLost, RpcError) as e:
                logging.error(
                    "Moonraker connection on %s failed: %s", self.socket_path, e
                )
            if identified:
                delay = self.reconnect_delay_min
            logging.info("Reconnecting to Moonraker in %.0f s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.reconnect_delay_max)

    async def _session(self):
        reader, writer = await asyncio.open_unix_connection(
            self.socket_path, limit=STREAM_LIMIT
        )
        self._writer = writer
        queue = asyncio.Queue()
        read_task = asyncio.ensure_future(self._read_loop(reader, queue))
        work_task = asyncio.ensure_future(self._work_loop(queue))
        identified = False
        try:
            await self.call("server.connection.identify", self.identity)
            identified = True
            logging.info("Identified to Moonraker as agent %r", self.identity["client_name"])
            await self.on_connected()
            done, _ = await asyncio.wait(
                [read_task, work_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            self._writer = None
            for task in (read_task, work_task):
                task.cancel()
            self._fail_pending()
            writer.close()
        return identified

    def _fail_pending(self):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionLost("Moonraker connection closed"))
        self._pending = {}

    async def call(self, method, params=None):
        if self._writer is None:
            raise ConnectionLost("not connected to Moonraker")
        request_id = next(self._ids)
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        message = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        try:
            await self._send(message)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message):
        writer = self._writer
        if writer is None:
            raise ConnectionLost("not connected to Moonraker")
        data = json.dumps(message).encode("utf-8") + ETX
        async with self._write_lock:
            writer.write(data)
            await writer.drain()

    async def _read_loop(self, reader, queue):
        try:
            await self._read_frames(reader, queue)
        finally:
            self._fail_pending()

    async def _read_frames(self, reader, queue):
        while True:
            try:
                frame = await reader.readuntil(ETX)
            except asyncio.IncompleteReadError:
                return
            try:
                message = json.loads(frame[:-1].decode("utf-8"))
            except ValueError as e:
                logging.error("Discarding malformed message from Moonraker: %s", e)
                continue
            self._route(message, queue)

    def _route(self, message, queue):
        if not isinstance(message, dict):
            logging.error("Discarding non-object message from Moonraker: %r", message)
            return
        if "method" in message:
            queue.put_nowait(message)
            return
        future = self._pending.get(message.get("id"))
        if future is None:
            logging.error("Discarding response with unknown id: %r", message)
            return
        if future.done():
            return
        if "error" in message:
            error = message["error"]
            if not isinstance(error, dict):
                error = {"code": RPC_INTERNAL_ERROR, "message": str(error)}
            future.set_exception(
                RpcError(
                    error.get("code", RPC_INTERNAL_ERROR),
                    error.get("message", "unknown error"),
                    error.get("data"),
                )
            )
        else:
            future.set_result(message.get("result"))

    async def _work_loop(self, queue):
        while True:
            message = await queue.get()
            method = message["method"]
            params = message.get("params")
            if "id" in message:
                await self._answer(message["id"], method, params)
                continue
            try:
                await self.handle_notification(method, params)
            except (ConnectionLost, asyncio.CancelledError):
                raise
            except Exception:
                logging.exception("Handling notification %s failed", method)

    async def _answer(self, request_id, method, params):
        try:
            result = await self.handle_request(method, params)
            # Moonraker reads a null result as an agent error
            # (moonraker/common.py:821-825), so every success returns a value.
            if result is None:
                raise RpcError(
                    RPC_INTERNAL_ERROR, "method %s returned no result" % (method,)
                )
            response = {"jsonrpc": "2.0", "result": result, "id": request_id}
        except RpcError as e:
            response = {
                "jsonrpc": "2.0",
                "error": {"code": e.code, "message": e.message},
                "id": request_id,
            }
        except (ConnectionLost, asyncio.CancelledError):
            raise
        except Exception as e:
            logging.exception("Request %s failed", method)
            response = {
                "jsonrpc": "2.0",
                "error": {
                    "code": RPC_INTERNAL_ERROR,
                    "message": "Internal error in %s: %s: %s. Check the"
                    " service log for the traceback."
                    % (method, type(e).__name__, e),
                },
                "id": request_id,
            }
        await self._send(response)
