// Hotendchanger web page entry point
import { aliases, icons } from "./icons.js";
import { websocketUrl } from "./moonraker.js";
import { SERVICE_UNIT, createPrinter } from "./printer.js";
import { klippyMessageParts, klippyStateColor, sortedTools } from "./state.js";
import FilamentLibrary from "./components/FilamentLibrary.js";
import ToolCard from "./components/ToolCard.js";

// Mainsail's dark theme colours: Vuetify 2 dark defaults and Mainsail's
// primary (mainsail src/store/variables.ts:6).
const MAINSAIL_DARK = {
  dark: true,
  colors: {
    background: "#121212",
    surface: "#1e1e1e",
    primary: "#2196f3",
  },
};

const printer = createPrinter(websocketUrl(window.location));
const MAINSAIL_URL = "/";

let leavePending = false;

function overlayActive() {
  return document.querySelector(".v-overlay--active") !== null;
}

function armLeaveOnEscape(event) {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  // Vuetify closes the topmost overlay from its own keydown listener while
  // v-overlay--active is set (vendor/vuetify-3.13.7/vuetify.min.js, VOverlay
  // onKeydown); this capture listener on window runs before it.
  if (overlayActive()) {
    leavePending = false;
    return;
  }
  event.preventDefault();
  leavePending = true;
}

// Chrome cancels a navigation started during the Escape keydown, since Escape stops page loading.
function leaveOnEscapeRelease(event) {
  if (event.key !== "Escape" || !leavePending) return;
  leavePending = false;
  if (overlayActive()) return;
  window.location.assign(MAINSAIL_URL);
}

function cancelLeaveOnEscape() {
  leavePending = false;
}

const App = {
  components: { FilamentLibrary, ToolCard },
  data() {
    return { icons, state: printer.state, serviceUnit: SERVICE_UNIT, mainsailUrl: MAINSAIL_URL };
  },
  mounted() {
    window.addEventListener("keydown", armLeaveOnEscape, { capture: true });
    window.addEventListener("keyup", leaveOnEscapeRelease, { capture: true });
    window.addEventListener("blur", cancelLeaveOnEscape);
  },
  unmounted() {
    window.removeEventListener("keydown", armLeaveOnEscape, { capture: true });
    window.removeEventListener("keyup", leaveOnEscapeRelease, { capture: true });
    window.removeEventListener("blur", cancelLeaveOnEscape);
  },
  computed: {
    socketChip() {
      const socket = this.state.socket;
      if (socket === "connected") return { text: "connected", color: "success" };
      if (socket === "connecting") return { text: "connecting", color: "warning" };
      if (socket === "disconnected") return { text: "disconnected", color: "error" };
      if (socket === "unauthorized") return { text: "not authorized", color: "error" };
      throw new Error("unhandled socket state " + socket);
    },
    klippyChip() {
      const s = this.state.klippy.state;
      return { text: s ?? "unknown", color: klippyStateColor(s) };
    },
    klippyMessage() {
      return klippyMessageParts(this.state.klippy.message || "Klipper is not ready.");
    },
    serviceChip() {
      const c = this.state.service.connected;
      if (c === true) return { text: "connected", color: "success" };
      if (c === false) return { text: "not connected", color: "error" };
      return { text: "unknown", color: "grey" };
    },
    tools() {
      return this.state.changer ? sortedTools(this.state.changer.tools) : [];
    },
  },
  template: `
    <v-app>
      <v-app-bar density="compact" flat class="hc-appbar">
        <v-icon :icon="icons.tool" class="ml-4 mr-3" />
        <v-app-bar-title>Hotendchanger</v-app-bar-title>
        <v-btn icon="$close" :href="mainsailUrl" variant="text" class="mr-2" title="Back to Mainsail" aria-label="Back to Mainsail" />
      </v-app-bar>
      <v-main>
        <v-container fluid>
          <div class="hc-status mb-4">
            <v-chip size="small" variant="tonal" :color="socketChip.color">Moonraker: {{ socketChip.text }}</v-chip>
            <v-chip size="small" variant="tonal" :color="klippyChip.color">Klipper: {{ klippyChip.text }}</v-chip>
            <v-chip size="small" variant="tonal" :color="serviceChip.color">Filament service: {{ serviceChip.text }}</v-chip>
          </div>

          <v-alert v-if="state.socket === 'unauthorized'" type="error" variant="tonal" class="mb-4" title="Moonraker refused this page">
            Add the network this device is on to <code>trusted_clients</code> in the <code>[authorization]</code> section of moonraker.conf, then restart Moonraker.
            This page has no login and works only from a network Moonraker trusts.
          </v-alert>
          <v-alert v-else-if="state.socket === 'disconnected'" type="error" variant="tonal" class="mb-4" title="Moonraker is not reachable">
            Retrying automatically. Check that Moonraker is running on the printer.
          </v-alert>

          <v-alert v-if="state.socket === 'connected' && state.klippy.state !== null && state.klippy.state !== 'ready'"
            type="warning" variant="tonal" class="mb-4" :title="'Klipper: ' + state.klippy.state">
            <div class="text-subtitle-2">{{ klippyMessage.headline }}</div>
            <div v-if="klippyMessage.detail">{{ klippyMessage.detail }}</div>
          </v-alert>
          <v-alert v-if="state.changerMissing" type="error" variant="tonal" class="mb-4" title="No hotendchanger section">
            Add the [hotendchanger] section to your Klipper config, then restart Klipper.
          </v-alert>
          <v-alert v-if="state.service.connected === false" type="error" variant="tonal" class="mb-4" title="Filament service not connected">
            Check the service with <code>systemctl status {{ serviceUnit }}</code>. The filament library and tool assignments are unavailable until it connects.
          </v-alert>

          <div v-if="tools.length" class="hc-tool-grid">
            <tool-card v-for="tool in tools" :key="tool.name" :tool="tool" />
          </div>

          <filament-library v-if="state.service.connected === true" />
        </v-container>
      </v-main>
    </v-app>
  `,
};

const vuetify = Vuetify.createVuetify({
  theme: { defaultTheme: "mainsailDark", themes: { mainsailDark: MAINSAIL_DARK } },
  icons: { defaultSet: "mdi", aliases },
});

Vue.createApp(App).provide("printer", printer).use(vuetify).mount("#app");
printer.start();
