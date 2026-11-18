// One card per configured tool
import { icons } from "../icons.js";
import ColorDialog from "./ColorDialog.js";
import {
  CHANGER_CHANGING,
  changeBlockedByPrint,
  changerStateColor,
  formatTemperatures,
  pinText,
  presenceChip,
} from "../state.js";

export default {
  name: "ToolCard",
  components: { ColorDialog },
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
      colorDialog: null,
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
      return presenceChip(this.tool.presence);
    },
    stateColor() {
      return changerStateColor(this.changer.state);
    },
    heater() {
      return this.state.heaters[this.tool.extruder] ?? null;
    },
    temperatureText() {
      if (this.heater === null) return "n/a";
      return formatTemperatures(this.heater.temperature, this.heater.target);
    },
    assignmentsKnown() {
      return this.state.assignments !== null;
    },
    assignment() {
      if (!this.assignmentsKnown) return undefined;
      return this.state.assignments.find((t) => t.number === this.tool.number);
    },
    filament() {
      return this.assignment === undefined ? undefined : this.assignment.filament;
    },
    toolColor() {
      return this.assignment === undefined ? null : this.assignment.color;
    },
    colorDialogOpen: {
      get() {
        return this.colorDialog !== null;
      },
      set(open) {
        if (!open) this.colorDialog = null;
      },
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
    heatHint() {
      if (!this.filament) return "Assign a filament to heat to its nozzle temperature";
      return "Heat to the filament's nozzle temperature (" + this.filament.nozzle_temp + " °C)";
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
        return {
          kind: "dock",
          label: "Dock",
          hint: "Dock this tool and leave the toolhead empty",
          script: "UNSELECT_TOOL",
        };
      }
      return {
        kind: "mount",
        label: "Mount",
        hint: "Mount this tool on the toolhead",
        script: "T" + this.tool.number,
      };
    },
  },
  methods: {
    pinText,
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
    async clear() {
      this.assignBusy = true;
      this.assignError = null;
      const reply = await this.printer.assignFilament(this.tool.number, null, null);
      this.assignError = "error" in reply ? reply.error : null;
      this.assignBusy = false;
    },
    choose(filamentId) {
      if (filamentId === null) {
        this.clear();
        return;
      }
      const chosen = (this.state.filaments ?? []).find((f) => f.id === filamentId);
      this.assignError = null;
      this.colorDialog = {
        title: "Colour of " + (chosen ? chosen.name : filamentId) + " in " + this.tool.name,
        save: (color) => this.printer.assignFilament(this.tool.number, filamentId, color),
      };
    },
    editColor() {
      this.colorDialog = {
        title: "Colour of " + this.filament.name + " in " + this.tool.name,
        save: (color) => this.printer.setToolColor(this.tool.number, color),
      };
    },
  },
  template: `
    <v-card class="hc-panel">
      <div class="hc-panel-toolbar hc-tool-header">
        <span class="hc-tool-name text-subtitle-2" :title="tool.name">{{ tool.name }}</span>
        <v-chip :color="chip.color" size="small" variant="flat" class="hc-chip" :title="chip.label">{{ chip.label }}</v-chip>
        <span class="hc-tool-temp text-body-2" :title="'Nozzle temperature, current / target (' + tool.extruder + ')'">{{ temperatureText }}</span>
      </div>
      <v-card-text class="pa-3">
        <div v-if="filament" class="hc-filament">
          <button type="button" class="hc-swatch hc-swatch-button" :style="{ backgroundColor: toolColor }"
            :title="'Change the colour (' + toolColor + ')'" @click="editColor"></button>
          <div class="hc-filament-text">
            <div class="text-body-2 hc-ellipsis" :title="filament.name">{{ filament.name }}</div>
            <div class="text-caption text-medium-emphasis hc-ellipsis" :title="filament.material + ', ' + filament.nozzle_temp + ' °C'">{{ filament.material }}, {{ filament.nozzle_temp }} °C</div>
          </div>
        </div>
        <div v-else-if="filament === null" class="hc-filament">
          <button type="button" class="hc-swatch hc-swatch-empty hc-swatch-button" disabled
            title="Assign a filament to set its colour"></button>
          <div class="text-body-2 text-medium-emphasis">No filament loaded</div>
        </div>
        <div v-else class="hc-filament">
          <div class="text-body-2 text-medium-emphasis">Filament assignment unavailable</div>
        </div>

        <v-select
          class="mt-3 hc-tool-select"
          label="Filament"
          density="compact"
          variant="outlined"
          hide-details
          :items="filamentItems"
          :model-value="selectedFilament"
          :disabled="!canEditAssignment"
          :loading="assignBusy"
          @update:model-value="choose"
        />
        <v-alert v-if="assignError" type="error" variant="tonal" density="compact" class="mt-2 hc-message">{{ assignError }}</v-alert>

        <div class="hc-actions mt-3">
          <div class="hc-action-buttons">
            <v-btn
              color="primary"
              variant="tonal"
              size="small"
              density="comfortable"
              :prepend-icon="icons.fire"
              :title="heatHint"
              :disabled="!filament || busy !== null"
              :loading="busy === 'heat'"
              @click="heat"
            >Heat</v-btn>
            <v-btn
              variant="tonal"
              size="small"
              density="comfortable"
              :prepend-icon="icons.power"
              title="Turn the nozzle heater off"
              :disabled="busy !== null"
              :loading="busy === 'off'"
              @click="off"
            >Off</v-btn>
            <v-btn
              color="primary"
              variant="flat"
              size="small"
              density="comfortable"
              :prepend-icon="icons.mount"
              :title="changeAction.hint"
              :disabled="changeBlockReason !== null || busy !== null"
              :loading="busy === changeAction.kind"
              @click="change"
            >{{ changeAction.label }}</v-btn>
          </div>
          <v-menu location="bottom end" :close-on-content-click="false">
            <template #activator="{ props }">
              <v-chip
                v-bind="props"
                class="hc-details-button px-1"
                size="small"
                density="comfortable"
                label
                variant="outlined"
                color="grey"
                title="Details"
                aria-label="Details"
                role="button"
              ><v-icon size="small" :icon="icons.info" /></v-chip>
            </template>
            <v-card class="hc-details text-caption pa-3">
              <div class="hc-row"><span class="hc-label">Changer state</span>
                <span :class="['hc-ellipsis', 'text-' + stateColor]" :title="changer.state">{{ changer.state }}</span></div>
              <div class="hc-row"><span class="hc-label">Dock pin</span>
                <span class="hc-ellipsis">{{ pinText(tool.detect) }}</span></div>
              <div class="hc-row"><span class="hc-label">Toolhead pin</span>
                <span class="hc-ellipsis">{{ pinText(changer.toolhead_detect) }}</span></div>
              <div class="hc-row"><span class="hc-label">Nozzle heater</span>
                <span class="hc-ellipsis" :title="tool.extruder">{{ tool.extruder }}</span></div>
            </v-card>
          </v-menu>
        </div>
        <div v-if="changeBlockReason" class="text-caption text-medium-emphasis mt-1">{{ changeAction.label }} unavailable: {{ changeBlockReason }}</div>
        <v-alert v-if="gcodeError" type="error" variant="tonal" density="compact" class="mt-2 hc-message">{{ gcodeError }}</v-alert>
      </v-card-text>
      <color-dialog
        v-if="colorDialog"
        v-model="colorDialogOpen"
        :title="colorDialog.title"
        :initial-color="toolColor"
        :save="colorDialog.save"
      />
    </v-card>
  `,
};
