// Add or edit one library filament
const HEX_COLOR = /^#?[0-9A-Fa-f]{6}$/;

function temperatureValue(text) {
  const trimmed = String(text).trim();
  const number = Number(trimmed);
  // A non-numeric entry is sent as typed so the service's validation
  // message names it.
  return trimmed !== "" && Number.isFinite(number) ? number : trimmed;
}

export default {
  name: "FilamentDialog",
  inject: ["printer"],
  props: {
    modelValue: { type: Boolean, required: true },
    filament: { type: Object, default: null },
  },
  emits: ["update:modelValue"],
  data() {
    return { form: this.blankForm(), saving: false, error: null };
  },
  computed: {
    title() {
      return this.filament ? "Edit filament" : "Add filament";
    },
    pickerColor() {
      const text = this.form.color.trim();
      if (!HEX_COLOR.test(text)) return null;
      return (text.startsWith("#") ? text : "#" + text).toUpperCase();
    },
    complete() {
      return (
        this.form.name.trim() !== "" &&
        this.form.material.trim() !== "" &&
        this.form.color.trim() !== "" &&
        String(this.form.nozzle_temp).trim() !== ""
      );
    },
  },
  watch: {
    modelValue(open) {
      if (open) {
        this.form = this.blankForm();
        this.error = null;
      }
    },
  },
  methods: {
    blankForm() {
      const f = this.filament;
      return {
        name: f ? f.name : "",
        material: f ? f.material : "",
        color: f ? f.color : "",
        nozzle_temp: f ? String(f.nozzle_temp) : "",
      };
    },
    pick(color) {
      if (typeof color === "string" && color.toUpperCase() !== this.pickerColor) {
        this.form.color = color.slice(0, 7).toUpperCase();
      }
    },
    close() {
      this.$emit("update:modelValue", false);
    },
    async save() {
      this.saving = true;
      this.error = null;
      const fields = {
        name: this.form.name,
        material: this.form.material,
        color: this.form.color,
        nozzle_temp: temperatureValue(this.form.nozzle_temp),
      };
      const reply = this.filament
        ? await this.printer.updateFilament(this.filament.id, fields)
        : await this.printer.createFilament(fields);
      this.saving = false;
      if ("error" in reply) {
        this.error = reply.error;
        return;
      }
      this.close();
    },
  },
  template: `
    <v-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" max-width="460">
      <v-card class="hc-panel">
        <v-toolbar density="compact" flat class="hc-panel-toolbar">
          <v-toolbar-title class="text-subtitle-1 ml-4">{{ title }}</v-toolbar-title>
        </v-toolbar>
        <v-card-text>
          <v-text-field v-model="form.name" label="Name" density="compact" variant="outlined" />
          <v-text-field v-model="form.material" label="Material" density="compact" variant="outlined" />
          <v-color-picker
            :model-value="pickerColor"
            @update:model-value="pick"
            mode="hex"
            :modes="['hex']"
            hide-inputs
            width="100%"
            class="mb-4"
          />
          <v-text-field v-model="form.color" label="Colour (RRGGBB)" density="compact" variant="outlined">
            <template #prepend-inner>
              <span class="hc-swatch hc-swatch-small" :class="{ 'hc-swatch-empty': !pickerColor }"
                :style="pickerColor ? { backgroundColor: pickerColor } : {}"></span>
            </template>
          </v-text-field>
          <v-text-field v-model="form.nozzle_temp" label="Nozzle temperature" suffix="°C"
            inputmode="decimal" density="compact" variant="outlined" />
          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="hc-message">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="close">Cancel</v-btn>
          <v-btn color="primary" variant="flat" :disabled="!complete" :loading="saving" @click="save">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  `,
};
