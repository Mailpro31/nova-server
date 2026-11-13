import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const ia: Page = {
  id: "ia",
  path: "/ia",
  async render() {
    return { title: "Coûts d'IA", body: '<p class="empty">À construire.</p>' };
  },
};
