// Display logic over the printer.hotendchanger and print_stats status objects

// The hotendchanger plugin's state set (hotendchanger.py STATE_*).
export const CHANGER_UNINITIALIZED = "uninitialized";
export const CHANGER_READY = "ready";
export const CHANGER_CHANGING = "changing";
export const CHANGER_ERROR = "error";
export const CHANGER_UNKNOWN = "unknown";

// Dock pin reading of a hotend sitting in its dock (hotendchanger.py
// describe_pin_state).
const PIN_TRIGGERED = "triggered";

export const CHIP_MOUNTED = { label: "Mounted", color: "success" };
export const CHIP_DOCKED = { label: "Docked", color: "info" };
export const CHIP_NOT_MOUNTED = { label: "Not mounted", color: "grey" };
export const CHIP_UNKNOWN = { label: "Unknown", color: "warning" };
export const CHIP_UNRECOGNIZED = { label: "Unrecognized state", color: "error" };

export function sortedTools(changerTools) {
  return Object.entries(changerTools)
    .map(([name, info]) => ({ name, ...info }))
    .sort((a, b) => a.number - b.number);
}

export function heaterNames(changerTools) {
  return [...new Set(Object.values(changerTools).map((t) => t.extruder))].sort();
}

function dockedOr(tool, fallback) {
  return tool.detect === PIN_TRIGGERED ? CHIP_DOCKED : fallback;
}

export function toolChip(tool, changer) {
  const state = changer.state;
  if (state === CHANGER_CHANGING) {
    return dockedOr(tool, CHIP_UNKNOWN);
  }
  if (changer.active_tool === tool.number) {
    return CHIP_MOUNTED;
  }
  if (state === CHANGER_READY) {
    return dockedOr(tool, CHIP_NOT_MOUNTED);
  }
  if (state === CHANGER_UNKNOWN) {
    return dockedOr(tool, CHIP_UNKNOWN);
  }
  if (state === CHANGER_UNINITIALIZED) {
    return dockedOr(tool, CHIP_UNKNOWN);
  }
  if (state === CHANGER_ERROR) {
    return dockedOr(tool, CHIP_UNKNOWN);
  }
  return CHIP_UNRECOGNIZED;
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

export function formatTemperature(value) {
  return typeof value === "number" ? value.toFixed(1) + " °C" : "n/a";
}
