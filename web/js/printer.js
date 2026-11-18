// Printer session: Moonraker connection, Klipper status and filament service
import {
  ConnectionClosed,
  MoonrakerSocket,
  RpcError,
  isUnauthorized,
} from "./moonraker.js";
import { heaterNames } from "./state.js";

const { reactive } = Vue;

// Filament service agent name and change event
// (service/filament_library.py AGENT_NAME, CHANGE_EVENT).
export const AGENT = "hotendchanger";
const CHANGE_EVENT = "library_changed";
export const SERVICE_UNIT = "hotendchanger-filament";

const CLIENT_NAME = "Hotendchanger";
const CLIENT_VERSION = "unversioned";
const CLIENT_URL = "https://github.com/jaak0b/kalico-hotendchanger";

const RECONNECT_DELAY_MIN_MS = 1000;
const RECONNECT_DELAY_MAX_MS = 30000;
const KLIPPY_POLL_MS = 2000;

const CHANGER_OBJECT = "hotendchanger";
const PRINT_STATS_OBJECT = "print_stats";
const HEATER_FIELDS = ["temperature", "target"];

function errorText(error) {
  if (error instanceof RpcError || error instanceof ConnectionClosed) {
    return error.message;
  }
  throw error;
}

export function createPrinter(url) {
  const state = reactive({
    socket: "connecting",
    klippy: { state: null, message: null },
    changer: null,
    changerMissing: false,
    heaters: {},
    printState: null,
    service: { connected: null },
    filaments: null,
    filamentsError: null,
    assignments: null,
    assignmentsError: null,
  });

  let subscribedHeaters = null;
  let klippyPoll = null;

  const socket = new MoonrakerSocket(
    url,
    { onOpen, onClose, onNotification },
    RECONNECT_DELAY_MIN_MS,
    RECONNECT_DELAY_MAX_MS
  );

  function clearKlipperData() {
    state.changer = null;
    state.changerMissing = false;
    state.heaters = {};
    state.printState = null;
    subscribedHeaters = null;
  }

  function stopKlippyPoll() {
    if (klippyPoll !== null) {
      clearTimeout(klippyPoll);
      klippyPoll = null;
    }
  }

  function scheduleKlippyPoll() {
    stopKlippyPoll();
    klippyPoll = setTimeout(() => {
      klippyPoll = null;
      refreshServerInfo().catch(reportUnexpected);
    }, KLIPPY_POLL_MS);
  }

  function reportUnexpected(error) {
    console.error(error);
  }

  async function onOpen() {
    state.socket = "connecting";
    try {
      await socket.call("server.connection.identify", {
        client_name: CLIENT_NAME,
        version: CLIENT_VERSION,
        type: "web",
        url: CLIENT_URL,
      });
    } catch (error) {
      if (isUnauthorized(error)) {
        state.socket = "unauthorized";
        return;
      }
      console.error("Identifying to Moonraker failed:", errorText(error));
      return;
    }
    socket.sessionEstablished();
    state.socket = "connected";
    try {
      await refreshServerInfo();
      await refreshService();
    } catch (error) {
      console.error("Loading the printer state failed:", errorText(error));
    }
  }

  function onClose() {
    stopKlippyPoll();
    if (state.socket !== "unauthorized") {
      state.socket = "disconnected";
    }
    state.klippy = { state: null, message: null };
    state.service.connected = null;
    clearKlipperData();
  }

  async function refreshServerInfo() {
    const info = await socket.call("server.info");
    const klippyState = info.klippy_state;
    if (klippyState === "ready") {
      await onKlippyReady();
      return;
    }
    state.klippy = { state: klippyState, message: await klippyMessage(info) };
    clearKlipperData();
    scheduleKlippyPoll();
  }

  async function klippyMessage(info) {
    if (!info.klippy_connected) {
      return null;
    }
    try {
      const printerInfo = await socket.call("printer.info");
      return printerInfo.state_message;
    } catch (error) {
      return "Klipper state message unavailable: " + errorText(error);
    }
  }

  async function onKlippyReady() {
    stopKlippyPoll();
    state.klippy = { state: "ready", message: null };
    await subscribe([]);
    if (state.changer !== null) {
      await subscribe(heaterNames(state.changer.tools));
    }
    await loadLibrary();
  }

  async function subscribe(heaters) {
    const objects = {
      [CHANGER_OBJECT]: null,
      [PRINT_STATS_OBJECT]: ["state"],
    };
    for (const name of heaters) {
      objects[name] = HEATER_FIELDS;
    }
    const result = await socket.call("printer.objects.subscribe", { objects });
    const status = result.status;
    subscribedHeaters = heaters;
    state.changerMissing = !(CHANGER_OBJECT in status);
    state.changer = status[CHANGER_OBJECT] ?? null;
    state.printState = status[PRINT_STATS_OBJECT]?.state ?? null;
    const readings = {};
    for (const name of heaters) {
      readings[name] = status[name] ?? null;
    }
    state.heaters = readings;
  }

  function sameNames(a, b) {
    return a.length === b.length && a.every((name, i) => name === b[i]);
  }

  function applyStatus(diff) {
    if (CHANGER_OBJECT in diff && state.changer !== null) {
      Object.assign(state.changer, diff[CHANGER_OBJECT]);
      if ("tools" in diff[CHANGER_OBJECT]) {
        const wanted = heaterNames(state.changer.tools);
        if (subscribedHeaters === null || !sameNames(wanted, subscribedHeaters)) {
          subscribe(wanted).catch((error) =>
            console.error("Resubscribing failed:", errorText(error))
          );
        }
      }
    }
    if (PRINT_STATS_OBJECT in diff && "state" in diff[PRINT_STATS_OBJECT]) {
      state.printState = diff[PRINT_STATS_OBJECT].state;
    }
    for (const name of subscribedHeaters ?? []) {
      if (name in diff) {
        state.heaters[name] = { ...(state.heaters[name] ?? {}), ...diff[name] };
      }
    }
  }

  function onAgentEvent(event) {
    if (event.agent !== AGENT) {
      // Events of other agents are broadcast to every client too.
      return;
    }
    if (event.event === "connected") {
      state.service.connected = true;
      loadLibrary().catch(reportUnexpected);
    } else if (event.event === "disconnected") {
      state.service.connected = false;
    } else if (event.event === CHANGE_EVENT) {
      loadLibrary().catch(reportUnexpected);
    } else {
      console.warn("Ignoring unknown filament service event:", event.event);
    }
  }

  function onNotification(method, params) {
    if (method === "notify_status_update") {
      applyStatus(params[0]);
    } else if (method === "notify_klippy_ready") {
      onKlippyReady().catch((error) =>
        console.error("Loading the Klipper state failed:", errorText(error))
      );
    } else if (method === "notify_klippy_shutdown") {
      refreshServerInfo().catch(reportUnexpected);
      loadLibrary().catch(reportUnexpected);
    } else if (method === "notify_klippy_disconnected") {
      state.klippy = { state: "disconnected", message: null };
      clearKlipperData();
      scheduleKlippyPoll();
      loadLibrary().catch(reportUnexpected);
    } else if (method === "notify_agent_event") {
      onAgentEvent(params[0]);
    } else {
      // Moonraker broadcasts every server notification to all clients
      // (moonraker/components/websockets.py:203-215); only the ones above
      // concern this page.
    }
  }

  async function refreshService() {
    const result = await socket.call("server.extensions.list");
    state.service.connected = result.agents.some((a) => a.name === AGENT);
    await loadLibrary();
  }

  async function agentRequest(method, args) {
    let reply;
    try {
      reply = await socket.call("server.extensions.request", {
        agent: AGENT,
        method,
        arguments: args,
      });
    } catch (error) {
      return { error: "Moonraker request failed: " + errorText(error) };
    }
    if (reply !== null && typeof reply === "object" && "error" in reply) {
      return { error: String(reply.error) };
    }
    if (reply !== null && typeof reply === "object" && "result" in reply) {
      return { result: reply.result };
    }
    return {
      error:
        "The filament service sent a reply with neither result nor error: " +
        JSON.stringify(reply),
    };
  }

  async function loadLibrary() {
    if (state.service.connected !== true) {
      state.filaments = null;
      state.filamentsError = null;
      state.assignments = null;
      state.assignmentsError = null;
      return;
    }
    const [filaments, tools] = await Promise.all([
      agentRequest("filaments.list", {}),
      agentRequest("tools.list", {}),
    ]);
    if ("error" in filaments) {
      state.filaments = null;
      state.filamentsError = filaments.error;
    } else {
      state.filaments = filaments.result.filaments;
      state.filamentsError = null;
    }
    if ("error" in tools) {
      state.assignments = null;
      state.assignmentsError = tools.error;
    } else {
      state.assignments = tools.result.tools;
      state.assignmentsError = null;
    }
  }

  async function changeLibrary(method, args) {
    const reply = await agentRequest(method, args);
    await loadLibrary();
    return reply;
  }

  async function runGcode(script) {
    try {
      await socket.call("printer.gcode.script", { script });
      return null;
    } catch (error) {
      return errorText(error);
    }
  }

  return {
    state,
    start: () => socket.start(),
    runGcode,
    createFilament: (fields) => changeLibrary("filaments.create", fields),
    updateFilament: (id, fields) =>
      changeLibrary("filaments.update", { id, ...fields }),
    deleteFilament: (id) => changeLibrary("filaments.delete", { id }),
    assignFilament: (tool, filamentId) =>
      changeLibrary("tools.assign", { tool, filament_id: filamentId }),
  };
}
