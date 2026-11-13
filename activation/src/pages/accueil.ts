import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const accueil: Page = {
  id: "accueil",
  path: "/",
  async render() {
    return { title: "Accueil", body: '<p class="empty">À construire.</p>' };
  },
};
