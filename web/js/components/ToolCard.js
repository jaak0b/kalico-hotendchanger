// One card per configured tool
import { icons } from "../icons.js";
import {
  CHANGER_CHANGING,
  changeBlockedByPrint,
  changerStateColor,
  formatTemperature,
  toolChip,
} from "../state.js";

export default {
  name: "ToolCard",
  inject: ["printer"],
  props: {
    tool: { type: Object, required: true },
  },
  data() {
    return {
      icons,
      busy: null,
      gcodeError: null,
      assignBusy: false,
      assignError: null,
    };
  },
  computed: {
    state() {
      return this.printer.state;
    },
    changer() {
      return this.state.changer;
    },
    chip() {
      return toolChip(this.tool, this.changer);
    },
    stateColor() {
      return changerStateColor(this.changer.state);
    },
    heater() {
      return this.state.heaters[this.tool.extruder] ?? null;
    },
    temperatureText() {
      if (this.heater === null) return "n/a";
      return (
        formatTemperature(this.heater.temperature) +
        " / " +
        formatTemperature(this.heater.target)
      );
    },
    assignmentsKnown() {
      return this.state.assignments !== null;
    },
    filament() {
      if (!this.assignmentsKnown) return undefined;
      const entry = this.state.assignments.find((t) => t.number === this.tool.number);
      return entry === undefined ? undefined : entry.filament;
    },
    filamentItems() {
      if (this.filament === undefined) return [];
      const items = [{ title: "No filament loaded", value: null }];
      for (const f of this.state.filaments ?? []) {
        items.push({ title: f.name + " (" + f.material + ")", value: f.id });
      }
      return items;
    },
    selectedFilament() {
      return this.filament ? this.filament.id : null;
    },
    canEditAssignment() {
      return (
        this.assignmentsKnown && this.state.filaments !== null && !this.assignBusy
      );
    },
    mounted() {
      return this.changer.active_tool === this.tool.number;
    },
    changeBlockReason() {
      if (this.changer.state === CHANGER_CHANGING) {
        return "A tool change is in progress.";
      }
      return changeBlockedByPrint(this.state.printState);
    },
    changeAction() {
      if (this.mounted) {
        return { kind: "dock", label: "Dock", script: "UNSELECT_TOOL" };
      }
      return { kind: "mount", label: "Mount", script: "T" + this.tool.number };
    },
  },
  methods: {
    async gcode(kind, script) {
      this.busy = kind;
      this.gcodeError = null;
      this.gcodeError = await this.printer.runGcode(script);
      this.busy = null;
    },
    heat() {
      this.gcode(
        "heat",
        "SET_HEATER_TEMPERATURE HEATER=" +
          this.tool.extruder +
          " TARGET=" +
          this.filament.nozzle_temp
      );
    },
    off() {
      this.gcode(
        "off",
        "SET_HEATER_TEMPERATURE HEATER=" + this.tool.extruder + " TARGET=0"
      );
    },
    change() {
      this.gcode(this.changeAction.kind, this.changeAction.script);
    },
    async assign(filamentId) {
      this.assignBusy = true;
      this.assignError = null;
      const reply = await this.printer.assignFilament(this.tool.number, filamentId);
      this.assignError = "error" in reply ? reply.error : null;
      this.assignBusy = false;
    },
  },
  template: `
    <v-card class="hc-panel">
      <v-toolbar density="compact" flat class="hc-panel-toolbar">
        <v-icon :icon="icons.tool" class="ml-4 mr-2" size="small" />
        <v-toolbar-title class="text-subtitle-1">{{ tool.name }}</v-toolbar-title>
        <v-chip :color="chip.color" size="small" variant="flat" class="mr-4">{{ chip.label }}</v-chip>
      </v-toolbar>
      <v-card-text>
        <div class="hc-row"><span class="hc-label">Changer state</span>
          <span :class="'text-' + stateColor">{{ changer.state }}</span></div>
        <div class="hc-row"><span class="hc-label">Nozzle ({{ tool.extruder }})</span>
          <span>{{ temperatureText }}</span></div>

        <div v-if="filament" class="hc-filament mt-3">
          <span class="hc-swatch" :style="{ backgroundColor: filament.color }"></span>
          <div>
            <div class="text-body-1">{{ filament.name }}</div>
            <div class="text-caption text-medium-emphasis">{{ filament.material }}, {{ filament.nozzle_temp }} °C</div>
          </div>
        </div>
        <div v-else-if="filament === null" class="hc-filament hc-filament-empty mt-3">
          <span class="hc-swatch hc-swatch-empty"></span>
          <div class="text-medium-emphasis">No filament loaded</div>
        </div>
        <div v-else class="hc-filament hc-filament-empty mt-3">
          <div class="text-medium-emphasis">Filament assignment unavailable</div>
        </div>

        <v-select
          class="mt-3"
          label="Filament"
          density="compact"
          variant="outlined"
          hide-details
          :items="filamentItems"
          :model-value="selectedFilament"
          :disabled="!canEditAssignment"
          :loading="assignBusy"
          @update:model-value="assign"
        />
        <v-alert v-if="assignError" type="error" variant="tonal" density="compact" class="mt-2 hc-message">{{ assignError }}</v-alert>

        <div class="hc-actions mt-3">
          <v-btn
            color="primary"
            variant="tonal"
            size="small"
            :prepend-icon="icons.fire"
            :disabled="!filament || busy !== null"
            :loading="busy === 'heat'"
            @click="heat"
          >Heat to filament temperature</v-btn>
          <v-btn
            variant="tonal"
            size="small"
            :prepend-icon="icons.power"
            :disabled="busy !== null"
            :loading="busy === 'off'"
            @click="off"
          >Off</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            size="small"
            :prepend-icon="icons.mount"
            :disabled="changeBlockReason !== null || busy !== null"
            :loading="busy === changeAction.kind"
            @click="change"
          >{{ changeAction.label }}</v-btn>
        </div>
        <div v-if="changeBlockReason" class="text-caption text-medium-emphasis mt-1">{{ changeAction.label }} unavailable: {{ changeBlockReason }}</div>
        <v-alert v-if="gcodeError" type="error" variant="tonal" density="compact" class="mt-2 hc-message">{{ gcodeError }}</v-alert>
      </v-card-text>
    </v-card>
  `,
};
