import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const ecoles: Page = {
  id: "ecoles",
  path: "/ecoles",
  async render() {
    return { title: "Écoles", body: '<p class="empty">À construire.</p>' };
  },
};
