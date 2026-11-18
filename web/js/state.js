// Display logic over the printer.hotendchanger and print_stats status objects

// The hotendchanger plugin's state set (hotendchanger.py STATE_*).
export const CHANGER_UNINITIALIZED = "uninitialized";
export const CHANGER_READY = "ready";
export const CHANGER_CHANGING = "changing";
export const CHANGER_ERROR = "error";
export const CHANGER_UNKNOWN = "unknown";

// The plugin's per-tool presence set (hotendchanger.py PRESENCE_*).
const PRESENCE_CHIPS = {
  mounted: { label: "Mounted", color: "primary" },
  docked: { label: "Docked", color: "success" },
  not_mounted: { label: "Not mounted", color: "grey" },
  missing: { label: "Missing", color: "error" },
  conflict: { label: "Conflict", color: "error" },
  changing: { label: "Changing", color: "info" },
  unknown: { label: "Unknown", color: "warning" },
};

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

export function presenceChip(presence) {
  if (Object.hasOwn(PRESENCE_CHIPS, presence)) return PRESENCE_CHIPS[presence];
  // A missing or unrecognised value is shown as a problem rather than
  // hidden.
  return { label: String(presence), color: "error" };
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
