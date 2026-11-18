// Display logic over the printer.hotendchanger and print_stats status objects

// The hotendchanger plugin's state set (hotendchanger.py STATE_*).
export const CHANGER_UNINITIALIZED = "uninitialized";
export const CHANGER_READY = "ready";
export const CHANGER_CHANGING = "changing";
export const CHANGER_ERROR = "error";
export const CHANGER_UNKNOWN = "unknown";

// Detect pin readings (hotendchanger.py describe_optional_pin): null when
// the pin is not configured. A triggered dock pin means the hotend sits in
// its dock; a triggered toolhead pin means some hotend is on the toolhead.
const PIN_TRIGGERED = "triggered";
const PIN_UNTRIGGERED = "untriggered";

export const CHIP_UNKNOWN = { label: "Unknown", color: "warning" };
export const CHIP_CHANGING = { label: "Changing", color: "info" };
export const CHIP_DOCKED = { label: "Docked", color: "success" };
export const CHIP_CHECK_TOOL = { label: "Check tool", color: "error" };
export const CHIP_MOUNTED = { label: "Mounted", color: "primary" };
export const CHIP_NOT_DETECTED = { label: "Not detected", color: "warning" };
export const CHIP_MISSING = { label: "Missing", color: "error" };
export const CHIP_NOT_MOUNTED = { label: "Not mounted", color: "grey" };

export function sortedTools(changerTools) {
  return Object.entries(changerTools)
    .map(([name, info]) => ({ name, ...info }))
    .sort((a, b) => a.number - b.number);
}

export function heaterNames(changerTools) {
  return [...new Set(Object.values(changerTools).map((t) => t.extruder))].sort();
}

export function pinText(reading) {
  return reading === null ? "not configured" : String(reading);
}

function rawChip(value) {
  return { label: String(value), color: "error" };
}

function stateChip(state) {
  return { label: state, color: changerStateColor(state) };
}

function isChangerState(state) {
  if (state === CHANGER_UNINITIALIZED) return true;
  if (state === CHANGER_READY) return true;
  if (state === CHANGER_CHANGING) return true;
  if (state === CHANGER_ERROR) return true;
  if (state === CHANGER_UNKNOWN) return true;
  return false;
}

function isPinReading(reading) {
  if (reading === PIN_TRIGGERED) return true;
  if (reading === PIN_UNTRIGGERED) return true;
  if (reading === null) return true;
  return false;
}

// The plugin reads an untriggered toolhead pin as no hotend mounted
// (hotendchanger.py:1438-1443) but declares a tool loss, with state error,
// only after detect_settle_time (hotendchanger.py:928-931).
function activeToolChip(state, docked, toolhead) {
  if (docked) return CHIP_CHECK_TOOL;
  if (toolhead === PIN_TRIGGERED) return CHIP_MOUNTED;
  if (toolhead === null) return CHIP_MOUNTED;
  if (toolhead === PIN_UNTRIGGERED) {
    if (state === CHANGER_READY) return CHIP_NOT_DETECTED;
    if (state === CHANGER_ERROR) return stateChip(state);
    if (state === CHANGER_UNKNOWN) return stateChip(state);
    if (state === CHANGER_UNINITIALIZED) return stateChip(state);
    if (state === CHANGER_CHANGING) return stateChip(state);
    return rawChip(state);
  }
  return rawChip(toolhead);
}

export function toolChip(tool, changer) {
  const state = changer.state;
  const toolhead = changer.toolhead_detect;
  if (!isChangerState(state)) return rawChip(state);
  if (!isPinReading(tool.detect)) return rawChip(tool.detect);
  if (!isPinReading(toolhead)) return rawChip(toolhead);
  const docked = tool.detect === PIN_TRIGGERED;
  if (state === CHANGER_CHANGING) {
    return docked ? CHIP_DOCKED : CHIP_CHANGING;
  }
  if (changer.active_tool === tool.number) {
    return activeToolChip(state, docked, toolhead);
  }
  if (docked) return CHIP_DOCKED;
  if (
    changer.active_tool === null &&
    (state === CHANGER_UNKNOWN || toolhead === PIN_TRIGGERED)
  ) {
    return CHIP_UNKNOWN;
  }
  if (tool.detect === PIN_UNTRIGGERED) return CHIP_MISSING;
  if (tool.detect === null) return CHIP_NOT_MOUNTED;
  return rawChip(tool.detect);
}

export function changerStateColor(state) {
  if (state === CHANGER_READY) return "success";
  if (state === CHANGER_CHANGING) return "info";
  if (state === CHANGER_UNINITIALIZED) return "warning";
  if (state === CHANGER_UNKNOWN) return "warning";
  if (state === CHANGER_ERROR) return "error";
  // A state this page does not know is shown as a problem rather than
  // hidden.
  return "error";
}

// The print_stats state set, identical in Kalico
// (klippy/extras/print_stats.py:46-111) and stock Klipper
// (klippy/extras/print_stats.py:44-92).
export function changeBlockedByPrint(printState) {
  if (printState === "printing") return "A print is running.";
  if (printState === "paused") return "A print is paused.";
  if (printState === "standby") return null;
  if (printState === "complete") return null;
  if (printState === "cancelled") return null;
  if (printState === "error") return null;
  if (printState === null) return "The print state is not known yet.";
  return "Unrecognized print state " + JSON.stringify(printState) + ".";
}

// Moonraker's klippy_state values (moonraker/common.py:138-143).
export function klippyStateColor(state) {
  if (state === "ready") return "success";
  if (state === "startup") return "warning";
  if (state === "shutdown") return "error";
  if (state === "error") return "error";
  if (state === "disconnected") return "error";
  if (state === null) return "grey";
  // A state this page does not know is shown as a problem rather than
  // hidden.
  return "error";
}

// Klipper's state messages carry a leading newline and hard line breaks for
// a terminal (Kalico klippy/printer.py:43-80 message_startup,
// message_shutdown).
export function klippyMessageParts(message) {
  const lines = message.split(/\r?\n/).filter((line) => line.trim() !== "");
  const words = lines.slice(1).join(" ").split(/\s+/).filter((word) => word !== "");
  return { headline: (lines[0] ?? "").trim(), detail: words.join(" ") };
}

function temperatureValue(value) {
  return typeof value === "number" ? value.toFixed(1) : "n/a";
}

export function formatTemperatures(current, target) {
  return temperatureValue(current) + " / " + temperatureValue(target) + " °C";
}
