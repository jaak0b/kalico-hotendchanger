// Pick the colour of the filament a tool holds
const HEX_COLOR = /^#?[0-9A-Fa-f]{6}$/;

export default {
  name: "ColorDialog",
  props: {
    modelValue: { type: Boolean, required: true },
    title: { type: String, required: true },
    initialColor: { type: String, default: null },
    save: { type: Function, required: true },
  },
  emits: ["update:modelValue"],
  data() {
    return { text: "", saving: false, error: null };
  },
  computed: {
    pickerColor() {
      const text = this.text.trim();
      if (!HEX_COLOR.test(text)) return null;
      return (text.startsWith("#") ? text : "#" + text).toUpperCase();
    },
  },
  watch: {
    modelValue: {
      immediate: true,
      handler(open) {
        if (open) {
          this.text = this.initialColor ?? "";
          this.error = null;
        }
      },
    },
  },
  methods: {
    pick(color) {
      if (typeof color === "string" && color.toUpperCase() !== this.pickerColor) {
        this.text = color.slice(0, 7).toUpperCase();
      }
    },
    close() {
      this.$emit("update:modelValue", false);
    },
    async confirm() {
      this.saving = true;
      this.error = null;
      const reply = await this.save(this.text);
      this.saving = false;
      if ("error" in reply) {
        this.error = reply.error;
        return;
      }
      this.close();
    },
  },
  template: `
    <v-dialog :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" max-width="400">
      <v-card class="hc-panel">
        <v-toolbar density="compact" flat class="hc-panel-toolbar">
          <v-toolbar-title class="text-subtitle-1 ml-4">{{ title }}</v-toolbar-title>
        </v-toolbar>
        <v-card-text>
          <v-color-picker
            :model-value="pickerColor"
            @update:model-value="pick"
            mode="hex"
            :modes="['hex']"
            hide-inputs
            width="100%"
            class="mb-4"
          />
          <v-text-field v-model="text" label="Colour (RRGGBB)" density="compact" variant="outlined">
            <template #prepend-inner>
              <span class="hc-swatch hc-swatch-small" :class="{ 'hc-swatch-empty': !pickerColor }"
                :style="pickerColor ? { backgroundColor: pickerColor } : {}"></span>
            </template>
          </v-text-field>
          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="hc-message">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="close">Cancel</v-btn>
          <v-btn color="primary" variant="flat" :disabled="pickerColor === null" :loading="saving" @click="confirm">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  `,
};
