import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const activite: Page = {
  id: "activite",
  path: "/activite",
  async render() {
    return { title: "Activité", body: '<p class="empty">À construire.</p>' };
  },
};
