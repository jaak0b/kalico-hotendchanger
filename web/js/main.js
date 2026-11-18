// Hotendchanger web page entry point
import { aliases, icons } from "./icons.js";
import { websocketUrl } from "./moonraker.js";
import { SERVICE_UNIT, createPrinter } from "./printer.js";
import { klippyStateColor, sortedTools } from "./state.js";
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

const App = {
  components: { FilamentLibrary, ToolCard },
  data() {
    return { icons, state: printer.state, serviceUnit: SERVICE_UNIT };
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
        <v-btn icon="$close" href="/" variant="text" class="mr-2" title="Back to Mainsail" aria-label="Back to Mainsail" />
      </v-app-bar>
      <v-main>
        <v-container fluid class="hc-container">
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
            type="warning" variant="tonal" class="mb-4 hc-message" :title="'Klipper: ' + state.klippy.state">{{ state.klippy.message || 'Klipper is not ready.' }}</v-alert>
          <v-alert v-if="state.changerMissing" type="error" variant="tonal" class="mb-4" title="No hotendchanger section">
            Add the [hotendchanger] section to your Klipper config, then restart Klipper.
          </v-alert>
          <v-alert v-if="state.service.connected === false" type="error" variant="tonal" class="mb-4" title="Filament service not connected">
            Check the service with <code>systemctl status {{ serviceUnit }}</code>. The filament library and tool assignments are unavailable until it connects.
          </v-alert>

          <v-row v-if="tools.length">
            <v-col v-for="tool in tools" :key="tool.name" cols="12" md="6" xl="4">
              <tool-card :tool="tool" />
            </v-col>
          </v-row>

          <v-row>
            <v-col cols="12">
              <filament-library v-if="state.service.connected === true" />
            </v-col>
          </v-row>
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
