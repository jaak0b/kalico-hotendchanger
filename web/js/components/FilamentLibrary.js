// Filament library table with add, edit and delete
import { icons } from "../icons.js";
import FilamentDialog from "./FilamentDialog.js";

export default {
  name: "FilamentLibrary",
  components: { FilamentDialog },
  inject: ["printer"],
  data() {
    return {
      icons,
      dialogOpen: false,
      editing: null,
      deleting: null,
      deleteBusy: false,
      deleteError: null,
    };
  },
  computed: {
    state() {
      return this.printer.state;
    },
    toolsByFilament() {
      const users = {};
      for (const tool of this.state.assignments ?? []) {
        if (tool.filament) {
          (users[tool.filament.id] ??= []).push(tool.name);
        }
      }
      return users;
    },
    deleteOpen: {
      get() {
        return this.deleting !== null;
      },
      set(open) {
        if (!open) this.deleting = null;
      },
    },
  },
  methods: {
    usedBy(filament) {
      if (this.state.assignments === null) return "unknown";
      return (this.toolsByFilament[filament.id] ?? []).join(", ") || "none";
    },
    add() {
      this.editing = null;
      this.dialogOpen = true;
    },
    edit(filament) {
      this.editing = filament;
      this.dialogOpen = true;
    },
    askDelete(filament) {
      this.deleteError = null;
      this.deleting = filament;
    },
    async confirmDelete() {
      this.deleteBusy = true;
      this.deleteError = null;
      const reply = await this.printer.deleteFilament(this.deleting.id);
      this.deleteBusy = false;
      if ("error" in reply) {
        this.deleteError = reply.error;
        return;
      }
      this.deleting = null;
    },
  },
  template: `
    <v-card class="hc-panel">
      <v-toolbar density="compact" flat class="hc-panel-toolbar">
        <v-icon :icon="icons.palette" class="ml-4 mr-2" size="small" />
        <v-toolbar-title class="text-subtitle-1">Filament library</v-toolbar-title>
        <v-btn class="mr-2" variant="text" size="small" :prepend-icon="icons.plus"
          :disabled="state.filaments === null" @click="add">Add</v-btn>
      </v-toolbar>
      <v-alert v-if="state.filamentsError" type="error" variant="tonal" density="compact" class="ma-4 hc-message">{{ state.filamentsError }}</v-alert>
      <v-alert v-if="state.assignmentsError" type="warning" variant="tonal" density="compact" class="ma-4 hc-message">Tool assignments unavailable: {{ state.assignmentsError }}</v-alert>
      <v-table v-if="state.filaments !== null" density="compact" class="hc-table">
        <thead>
          <tr>
            <th></th><th>Name</th><th>Material</th><th>Nozzle</th><th>Used by</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in state.filaments" :key="f.id">
            <td class="hc-swatch-cell"><span class="hc-swatch hc-swatch-small" :style="{ backgroundColor: f.color }"></span></td>
            <td>{{ f.name }}</td>
            <td>{{ f.material }}</td>
            <td class="text-no-wrap">{{ f.nozzle_temp }} °C</td>
            <td>{{ usedBy(f) }}</td>
            <td class="text-right text-no-wrap">
              <v-btn :icon="icons.pencil" variant="text" size="small" title="Edit" @click="edit(f)" />
              <v-btn :icon="icons.trash" variant="text" size="small" title="Delete" @click="askDelete(f)" />
            </td>
          </tr>
          <tr v-if="state.filaments.length === 0">
            <td colspan="6" class="text-medium-emphasis">The library is empty. Add a filament to assign it to a tool.</td>
          </tr>
        </tbody>
      </v-table>
    </v-card>

    <filament-dialog v-model="dialogOpen" :filament="editing" />

    <v-dialog v-model="deleteOpen" max-width="420">
      <v-card v-if="deleting" class="hc-panel">
        <v-toolbar density="compact" flat class="hc-panel-toolbar">
          <v-toolbar-title class="text-subtitle-1 ml-4">Delete filament</v-toolbar-title>
        </v-toolbar>
        <v-card-text>
          Delete {{ deleting.name }} from the library? This cannot be undone.
          <v-alert v-if="deleteError" type="error" variant="tonal" density="compact" class="mt-3 hc-message">{{ deleteError }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="deleting = null">Cancel</v-btn>
          <v-btn color="error" variant="flat" :loading="deleteBusy" @click="confirmDelete">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  `,
};
